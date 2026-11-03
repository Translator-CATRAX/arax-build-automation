"""Build, stage and propose ARAX's databases for one Tier0 graph.

Only the actions you pass are done:

    autobuild                         rehearse a build (the default)
    autobuild --build [ARTIFACT ...]  build and test everything, or these artifacts
    autobuild --stage [TARGET ...]    upload to every staging server, or these
    autobuild --pr                    open or update the RTX PR
    autobuild --record                commit and push the build record
    autobuild --full                  all of the above, after asking
    autobuild --rehearse ...          show the plan and check access; change nothing

Snakemake does the building and testing, so `snakemake` alone can only build.
The release steps are run from here, and passing a flag is what authorizes one.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "workflow" / "scripts"
TESTS = REPO / "tests"
sys.path[:0] = [str(SCRIPTS), str(SCRIPTS / "sources"), str(SCRIPTS / "report")]

import local_config  # noqa: E402


class Fail(Exception):
    """The requested action can't go ahead; the message says why."""


@dataclass(frozen=True)
class Artifact:
    """One release artifact. The filename's version is {version}."""
    name: str
    rule: str
    filename: str
    stats_kind: str
    config_keys: tuple[str, ...]
    tests: tuple[str, ...]


ARTIFACTS = (
    Artifact("autocomplete", "autocomplete", "autocomplete_v{version}_{tier0}.sqlite",
             "autocomplete", ("autocomplete",), ("test_autocomplete.py",)),
    Artifact("tier0-info-for-overlay", "overlay", "tier0-info-for-overlay_v{version}_{tier0}.sqlite",
             "tier0_info_for_overlay", ("tier0_sqlite", "kg2c_sqlite"), ("test_overlay.py",)),
    Artifact("curie_to_pmids", "curie_to_pmids", "curie_to_pmids_v{version}_{tier0}.sqlite",
             "curie_to_pmids", ("curie_to_pmids",), ("test_curie_to_pmids.py",)),
    Artifact("curie_ngd", "curie_ngd", "curie_ngd_v{version}_{tier0}.sqlite",
             "curie_ngd", ("curie_ngd",), ("test_curie_ngd.py",)),
    Artifact("fda_approved_drugs", "fda_drugs", "fda_approved_drugs_v{version}.pickle",
             "fda_approved_drugs", ("fda_approved_drugs",), ("test_fda_approved_drugs.py",)),
)
BY_NAME = {a.name: a for a in ARTIFACTS}
ALIASES = {"overlay": "tier0-info-for-overlay", "fda_drugs": "fda_approved_drugs"}
FIRST_VERSION = "1.0"
LOGIN_RE = re.compile(r"team-expander-[\w.-]+@")


# ------------------------------------------------------------------ prompts


def choose(question: str, options: dict[str, str]) -> str:
    """Ask until one of options' keys is given. No input available means cancel."""
    labels = ", ".join(f"[{key}] {label}" for key, label in options.items())
    while True:
        try:
            answer = input(f"{question}\n{labels}: ").strip().lower()
        except EOFError:
            print()
            return "c" if "c" in options else "n"
        if answer in options:
            return answer


def confirm(question: str) -> bool:
    return choose(question, {"y": "yes", "n": "no"}) == "y"


# ------------------------------------------------------------------ the build


class Build:
    """One graph version's build directory and how far it has got."""

    def __init__(self, config: dict):
        self.config = config
        self.version = str(config["graph_version"])
        self.tier0 = f"tier0-{self.version}"
        self.dir = Path(config["output_root"]) / self.version
        self.targets = config["staging"]["targets"]

    def filename(self, artifact: Artifact, version: str) -> str:
        return artifact.filename.format(version=version, tier0=self.tier0)

    def version_dirs(self) -> list[Path]:
        """Directories of rebuilt artifacts (v1.1, v1.2, ...), oldest first."""
        if not self.dir.is_dir():
            return []
        found = [d for d in self.dir.iterdir() if d.is_dir() and re.fullmatch(r"v\d+\.\d+", d.name)]
        return sorted(found, key=lambda d: version_key(d.name[1:]))

    def current(self, artifact: Artifact) -> Path:
        """The newest copy of an artifact; a symlink is only a test's input."""
        for directory in reversed(self.version_dirs()):
            for path in directory.iterdir():
                if not path.is_symlink() and file_version(artifact, path.name, self.tier0):
                    return path
        return self.dir / self.filename(artifact, FIRST_VERSION)

    def current_file(self, name: str) -> Path:
        """The newest copy of config_dbs.json or build_report.md."""
        for directory in reversed(self.version_dirs()):
            if (directory / name).exists():
                return directory / name
        return self.dir / name

    def md5_dirs(self) -> list[Path]:
        return [self.dir / "md5_sums", *(d / "md5_sums" for d in self.version_dirs())]

    def is_tested(self, path: Path) -> bool:
        """Whether this copy passed its tests: md5s are only written after they do."""
        results = path.parent / "test_results" / "artifacts.xml"
        md5 = path.parent / "md5_sums" / f"{path.name}.md5"
        if not (path.is_file() and results.is_file() and md5.is_file()):
            return False
        return path.stat().st_mtime <= results.stat().st_mtime <= md5.stat().st_mtime

    def missing(self) -> list[Artifact]:
        return [a for a in ARTIFACTS if not self.is_tested(self.current(a))]

    def stage_result(self, target: str) -> Path:
        return self.dir / "staged" / f"{target}.json"

    def is_released(self) -> bool:
        """Whether this build has left the machine, so its files must not change."""
        if (self.dir / "rtx_pr.json").exists():
            return True
        return any(self.stage_result(target).exists() for target in self.targets)

    def next_version(self) -> str:
        """The next version unused both locally and on the database server."""
        names = [p.name for d in self.version_dirs() for p in d.iterdir()]
        names += [p.name for p in self.dir.iterdir()] if self.dir.is_dir() else []
        names += remote_listing(self.targets["arax_databases"], self.tier0)
        versions = [FIRST_VERSION, *(d.name[1:] for d in self.version_dirs())]
        for artifact in ARTIFACTS:
            versions += [v for name in names if (v := file_version(artifact, name, self.tier0))]
        major, minor = max(versions, key=version_key).split(".")
        return f"{major}.{int(minor) + 1}"


def version_key(version: str) -> tuple[int, int]:
    major, minor = version.split(".")
    return int(major), int(minor)


def file_version(artifact: Artifact, name: str, tier0: str) -> str | None:
    """The version in name, if name is a copy of artifact."""
    before, after = artifact.filename.format(version="{version}", tier0=tier0).split("{version}")
    pattern = re.escape(before) + r"(\d+\.\d+)" + re.escape(after)
    match = re.fullmatch(pattern, name)
    return match[1] if match else None


def remote_listing(target: dict, release: str) -> list[str]:
    directory = f"{target['root'].rstrip('/')}/{release}"
    result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", target["host"],
                             f"ls -1 {directory} 2>/dev/null || true"],
                            capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise Fail(f"Can't list {target['host']}:{directory} to pick an unused version: "
                   f"{result.stderr.strip()}")
    return result.stdout.split()


# ---------------------------------------------------------------- run things


def run_snakemake(*args: str) -> None:
    command = [sys.executable, "-m", "snakemake", *args]
    print(f"\n$ snakemake {' '.join(args)}", flush=True)
    if subprocess.run(command, cwd=REPO, check=False).returncode != 0:
        raise Fail("Snakemake failed; see its output above and the step's log, fix the cause and "
                   "rerun the same command.")


def run_logged(command: list, log: Path | None = None, check: bool = True) -> tuple[int, str]:
    """Run a command, showing its output and saving it to log without anyone's login."""
    if log:
        log.parent.mkdir(parents=True, exist_ok=True)
    with subprocess.Popen(list(map(str, command)), cwd=REPO, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True,
                          errors="replace") as process, \
            open(log or os.devnull, "a", encoding="utf-8") as out:
        lines = []
        for line in process.stdout:
            print(line, end="", flush=True)
            lines.append(line)
            out.write(LOGIN_RE.sub("", line))
    if check and process.returncode != 0:
        raise Fail(f"{Path(str(command[1])).name} failed; see the output above"
                   + (f" and {log}." if log else "."))
    return process.returncode, "".join(lines)


# --------------------------------------------------------------------- build


def do_build(build: Build, names: list[str], rehearse: bool, new_version: bool, yes: bool) -> None:
    """Build and test. Tests always run: md5s, staging and the PR all depend on them."""
    if build.is_released():
        if names:
            rebuild_as_new_version(build, names, rehearse, new_version, yes)
        else:
            print(f"Build {build.version} is already staged or proposed, so its files can't change. "
                  "To rebuild one, name it: autobuild --build ARTIFACT")
        return
    rules = [BY_NAME[name].rule for name in names]
    force = ["--forcerun", *rules] if rules else []
    if rehearse:
        run_snakemake("all", *force, "-n")
    else:
        run_snakemake("all", *force, "-c", str(build.config["_cores"]), "--rerun-incomplete")


def with_dependents(names: list[str]) -> list[str]:
    """curie_ngd is built from curie_to_pmids, so they must be rebuilt together."""
    if "curie_to_pmids" in names and "curie_ngd" not in names:
        return [*names, "curie_ngd"]
    return names


def rebuild_as_new_version(build: Build, names: list[str], rehearse: bool, new_version: bool,
                           yes: bool) -> None:
    """A staged file can't be replaced, and ARAX wouldn't download one with the same
    name, so a rebuild becomes the next version in its own directory."""
    version = build.next_version()
    extra = [n for n in with_dependents(names) if n not in names]
    names = with_dependents(names)
    listing = ", ".join(build.filename(BY_NAME[n], version) for n in names)
    print(f"Build {build.version} is already staged. The rebuild would be version {version}: {listing}")
    for name in extra:
        print(f"{name} is built from curie_to_pmids, so it has to be rebuilt too. Without it, the "
              "new curie_to_pmids would fail the tests against the old file.")
    if "curie_to_pmids" in names:
        print("curie_to_pmids uses the PubMed mirror as it is now, so its PubMed data can differ "
              "from the original build.")
    if rehearse:
        print("Rehearsal: nothing built.")
        return
    if not new_version:
        if yes:
            raise Fail("Creating a new version needs its own flag: add --new-version.")
        if not confirm(f"Rebuild as version {version}?"):
            raise Fail("Cancelled; nothing was built.")

    directory = build.dir / f"v{version}"
    directory.mkdir(parents=True)  # next_version() is unused, so this never overwrites
    print(f"Building in {directory}")
    paths = {name: directory / build.filename(BY_NAME[name], version) for name in names}
    for name in names:
        run_logged(builder_command(build, name, paths, directory),
                   directory / "logs" / f"{paths[name].name}.log")
        for leftover in (directory / "names.tsv", directory / "work"):
            if leftover.is_dir():
                shutil.rmtree(leftover)
            elif leftover.exists():
                leftover.unlink()
    if "curie_ngd" in names and "curie_to_pmids" not in names:
        # Its tests read curie_to_pmids; a link is never staged.
        source = build.current(BY_NAME["curie_to_pmids"])
        (directory / source.name).symlink_to(source)

    results = directory / "test_results" / "artifacts.xml"
    results.parent.mkdir(exist_ok=True)
    test_files = [TESTS / test for name in names for test in BY_NAME[name].tests]
    run_logged([sys.executable, "-m", "pytest", *test_files, "-p", "no:cacheprovider",
                "--build-dir", directory, "--graph-dir", graph_dir(build), "--babel", babel_sqlite(build),
                "--biolink-cache-dir", biolink_cache(build), "--junitxml", results],
               directory / "logs" / "test_artifacts.log")

    md5_dir = directory / "md5_sums"
    md5_dir.mkdir()
    for path in paths.values():
        digest = subprocess.run(["md5sum", path.name], cwd=directory, capture_output=True, text=True,
                                check=True).stdout
        (md5_dir / f"{path.name}.md5").write_text(digest)

    write_config_dbs(build, names, paths, directory)
    write_version_report(build, version, names, paths, directory, results)
    print(f"Built and tested version {version}. Next: autobuild --stage, then autobuild --pr")
    warn_about_symlink_script()


def graph_dir(build: Build) -> Path:
    return Path(build.config["graph_root"]) / build.version


def biolink_cache(build: Build) -> Path:
    return Path(build.config["output_root"]) / ".cache" / "biolink"


def babel_sqlite(build: Build) -> Path:
    """The Babel sqlite the Snakefile would pick; it must already be local."""
    import babel
    metadata = json.loads((graph_dir(build) / "graph-metadata.json").read_text())
    babel_version = metadata.get("translator:babelVersion") or metadata["babelVersion"]
    path, release = babel.resolve(Path(build.config["babel_dir"]), babel_version,
                                  build.config["babel_releases"])
    if release:
        raise Fail(f"{path} isn't here yet; get it with: snakemake -c1 {path}")
    return path


def builder_command(build: Build, name: str, paths: dict[str, Path], directory: Path) -> list:
    """The same builds the rules run, writing to this version's directory."""
    python = sys.executable
    graph = graph_dir(build)
    scripts = SCRIPTS
    output = paths[name]
    nodes, edges = graph / "nodes.jsonl", graph / "edges.jsonl"
    if name == "autocomplete":
        tsv = directory / "names.tsv"
        return ["sh", "-c", f'"{python}" "{scripts}/autocomplete/extract_names_ids.py" "{nodes}" "{tsv}"'
                            f' && "{python}" "{scripts}/autocomplete/create_load_db.py" -i "{tsv}" -o "{output}"']
    if name == "tier0-info-for-overlay":
        return [python, scripts / "overlay" / "generate_sqlite.py", "--nodes", nodes, "--edges", edges,
                "--graph-metadata", graph / "graph-metadata.json", "--output-dir", directory,
                "--tier0-build-date", build.version,
                "--schema-version", output.name.split("_v")[1].split("_")[0],
                "--biolink-cache-dir", biolink_cache(build)]
    if name == "curie_to_pmids":
        work = directory / "work"
        work.mkdir(exist_ok=True)
        return ["sh", "-c",
                f'"{python}" "{scripts}/curie_pmids/build_ngd_database.py" --full --skip-download'
                f' --pubmed-dir "{build.config["pubmed_dir"]}" --babel-db "{babel_sqlite(build)}"'
                f' --tier0-edges "{edges}" --output-dir "{work}"'
                f' && mv "{work}/curie_to_pmids.sqlite" "{output}"']
    if name == "curie_ngd":
        pmids = paths.get("curie_to_pmids") or build.current(BY_NAME["curie_to_pmids"])
        return [python, scripts / "curie_ngd" / "build_curie_ngd.py", "--curie-to-pmids", pmids,
                "--nodes", nodes, "--edges", edges, "--output", output,
                "--workers", build.config["_cores"]]
    if name == "fda_approved_drugs":
        drugbank = Path(build.config["drugbank_dir"]) / f"drugbank-{build.config['drugbank_version']}.xml"
        if not drugbank.exists():
            raise Fail(f"{drugbank} isn't here; get it with: snakemake -c1 {drugbank}")
        return [python, scripts / "fda_drugs" / "generate_fda_pickle.py", "--drugbank_xml", drugbank,
                "--babel_db", babel_sqlite(build), "--output_pickle", output]
    raise AssertionError(name)


def write_config_dbs(build: Build, names: list[str], paths: dict[str, Path], directory: Path) -> None:
    """The newest config_dbs.json with only the rebuilt artifacts' entries changed."""
    config = json.loads(build.current_file("config_dbs.json").read_text())
    root = build.config["arax_db_root"].rstrip("/")
    for name in names:
        for key in BY_NAME[name].config_keys:
            config["database_downloads"][key] = f"{root}/{build.tier0}/{paths[name].name}"
    (directory / "config_dbs.json").write_text(json.dumps(config, indent=3) + "\n")


def write_version_report(build: Build, version: str, names: list[str], paths: dict[str, Path],
                         directory: Path, results: Path) -> None:
    from artifact_stats import artifact_stats
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for suite in ET.parse(results).getroot().iter("testsuite"):
        for key in totals:
            totals[key] += int(suite.get(key, 0))
    stats = artifact_stats({BY_NAME[name].stats_kind: paths[name] for name in names})
    previous = {name: previous_copy(build, BY_NAME[name], directory).name for name in names}
    lines = [f"# ARAX database rebuild {build.version}, version {version}", "",
             f"Written {datetime.now().astimezone():%Y-%m-%d %H:%M %Z}. The rest of the build is unchanged.",
             "", "## Rebuilt", "", "| Artifact | File | Replaces |", "|---|---|---|"]
    for name in names:
        lines.append(f"| {name} | `{paths[name].name}` | `{previous[name]}` |")
    lines += ["", f"Tests: {totals['tests'] - totals['failures'] - totals['errors'] - totals['skipped']} passed, "
                  f"{totals['failures'] + totals['errors']} failed, {totals['skipped']} skipped.",
              "", "## Counts", "", "| Artifact | Count | Value |", "|---|---|---:|"]
    for name in names:
        for metric, value in stats[BY_NAME[name].stats_kind].items():
            lines.append(f"| {name} | {metric} | {value:,} |")
    (directory / "build_report.md").write_text("\n".join(lines) + "\n")


def previous_copy(build: Build, artifact: Artifact, directory: Path) -> Path:
    """The newest copy of an artifact from before the version in directory."""
    for earlier in reversed([d for d in build.version_dirs() if d != directory]):
        for path in earlier.iterdir():
            if not path.is_symlink() and file_version(artifact, path.name, build.tier0):
                return path
    return build.dir / build.filename(artifact, FIRST_VERSION)


def warn_about_symlink_script() -> None:
    print("Note: RTX's code/generate-db-symlinks.sh hardcodes artifact filenames. If this release "
          "uses it, update its link lines for the new version by hand.")


# --------------------------------------------------------------------- stage


def public_location(target: dict, release: str) -> str:
    """Where a release went, without anyone's login: this ends up in the public build record."""
    if target.get("sftp"):
        host = target["host"].rsplit("@", 1)[-1]
        return f"{host}:{target.get('databases', 'databases').rstrip('/')}/{release}"
    return f"{target['root'].rstrip('/')}/{release}"


def stage_files(build: Build, target: str, rehearse: bool) -> tuple[bool, bool]:
    """Stage or plan the current, tested files on one target; (succeeded, uploaded anything)."""
    tested = [path for path in map(build.current, ARTIFACTS) if build.is_tested(path)]
    succeeded, uploaded = True, False
    for directory in sorted({path.parent for path in tested}):
        files = [path.name for path in tested if path.parent == directory]
        command = [sys.executable, REPO / "tools" / "stage_artifacts.py", "--tier0", build.version,
                   "--from-dir", directory, *files, "--targets", target,
                   "--dry-run" if rehearse else "-y"]
        log = None if rehearse else build.dir / "logs" / f"staged_{target}.log"
        code, output = run_logged(command, log, check=False)
        succeeded = succeeded and code == 0
        uploaded = uploaded or "All targets staged" in output
    return succeeded, uploaded


def do_stage(build: Build, targets: list[str], rehearse: bool, yes: bool) -> None:
    targets = targets or list(build.targets)
    if unset := local_config.missing_settings(build.config, keys=(), targets=targets):
        message = f"Missing settings: {', '.join(unset)}. {local_config.SETUP_HINT}"
        if not rehearse:
            raise Fail(message)
        print(message)
        targets = [t for t in targets if f"staging.targets.{t}.host" not in unset]

    missing = build.missing()
    names = ", ".join(a.name for a in missing)
    if missing and rehearse:
        print(f"Not built and tested yet, so not checked: {names}")
    elif missing:
        print(f"WARNING: these haven't been built and passed their tests, so they can't be staged: {names}")
        answer = "s" if yes else choose("What do you want to do?", {
            "b": "build them, then stage everything",
            "s": "stage everything else without them",
            "c": "cancel"})
        if answer == "c":
            raise Fail("Cancelled; nothing was staged.")
        if answer == "b":
            do_build(build, [], rehearse=False, new_version=False, yes=yes)
            if still_missing := build.missing():
                raise Fail("Still not built: " + ", ".join(a.name for a in still_missing))
            missing = []

    failed = []
    for target in targets:
        print(f"\n--- {target} ---", flush=True)
        if rehearse and len(missing) == len(ARTIFACTS):
            if not check_access(build.targets[target]):
                failed.append(target)
            continue
        succeeded, uploaded = stage_files(build, target, rehearse)
        if not succeeded:
            failed.append(target)
        elif not rehearse and not missing:
            # Only a complete release counts as staged, for --pr and --record.
            result = build.stage_result(target)
            result.parent.mkdir(exist_ok=True)
            result.write_text(json.dumps({
                "staged": True, "location": public_location(build.targets[target], build.tier0),
                "already_staged": not uploaded}) + "\n")
    if failed:
        raise Fail("Not staged on: " + ", ".join(failed) if not rehearse
                   else "Can't stage on: " + ", ".join(failed))


def check_access(target: dict) -> bool:
    """Whether a target can be reached, for a rehearsal with nothing to upload yet."""
    if target.get("sftp"):
        command = ["sftp", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-b", "-", target["host"]]
        result = subprocess.run(command, input="ls\n", text=True, capture_output=True, check=False)
    else:
        command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", target["host"], "true"]
        result = subprocess.run(command, text=True, capture_output=True, check=False)
    print(("can log in to " + target["host"]) if result.returncode == 0
          else f"CAN'T log in to {target['host']}: {result.stderr.strip()}")
    return result.returncode == 0


# ------------------------------------------------------------------- PR, record


def do_pr(build: Build, rehearse: bool) -> None:
    needed = [build.current_file("config_dbs.json"), build.dir / "build_report.md",
              build.dir / "test_results" / "config_dbs.xml"]
    if absent := [str(path) for path in needed if not path.exists()]:
        message = f"Nothing to propose yet; run autobuild --build first. Missing: {', '.join(absent)}"
        if not rehearse:
            raise Fail(message)
        print(message)
        return
    settings, target = build.config["rtx_pr"], build.targets["arax_databases"]
    command = [sys.executable, SCRIPTS / "arax" / "open_config_pr.py",
               "--config-dbs", build.current_file("config_dbs.json"),
               "--report", build.current_file("build_report.md"),
               "--md5-dir", *build.md5_dirs(), "--version", build.version,
               "--repo", settings["repo"], "--remote", settings["remote"], "--base", settings["base"],
               "--branch-prefix", settings["branch_prefix"], "--staged-host", target["host"],
               "--staged-dir", f"{target['root'].rstrip('/')}/{build.tier0}",
               "--result", build.dir / "rtx_pr.json"]
    log = None if rehearse else build.dir / "logs" / "rtx_pr.json.log"
    run_logged(command + ([] if rehearse else ["--push"]), log)
    if build.version_dirs():
        warn_about_symlink_script()


def record_problems(build: Build) -> list[str]:
    """What must have really happened before a build's record can be published."""
    problems = []
    for target in build.targets:
        result = build.stage_result(target)
        if not result.exists() or json.loads(result.read_text()).get("staged") is not True:
            problems.append(f"not staged on {target}")
    pr = build.dir / "rtx_pr.json"
    if not pr.exists() or not json.loads(pr.read_text()).get("url"):
        problems.append("no RTX PR")
    return problems


def do_record(build: Build, rehearse: bool) -> None:
    problems = record_problems(build)
    if rehearse:
        print("Record: would commit and push builds/" + build.version)
        for problem in problems:
            print(f"Before that, it needs: {problem}")
        print(subprocess.run(["git", "status", "-sb", "--porcelain"], cwd=REPO, capture_output=True,
                             text=True, check=False).stdout.splitlines()[0])
        return
    if problems:
        raise Fail("Can't publish the build record yet: " + "; ".join(problems))
    settings = build.config["build_record"]
    log = build.dir / "logs" / "push_build_record.log"
    run_logged([sys.executable, SCRIPTS / "report" / "record_build.py", "--build-dir", build.dir,
                "--record-dir", REPO / "builds" / build.version], log)
    run_logged([sys.executable, SCRIPTS / "report" / "push_build_record.py", "--repo-dir", REPO,
                "--version", build.version,
                "--staged", *(build.stage_result(t) for t in build.targets),
                "--rtx-pr", build.dir / "rtx_pr.json", "--remote", settings["remote"],
                "--branch", settings["branch"], "--push", "--result", build.dir / "record_pushed.json"],
               log)


# ---------------------------------------------------------------------- main


def parse_args(argv: list[str] | None, target_names: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="autobuild", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    artifacts = [a.name for a in ARTIFACTS]
    parser.add_argument("--build", nargs="*", metavar="ARTIFACT",
                        help=f"Build and test everything, or just: {', '.join(artifacts)}.")
    parser.add_argument("--stage", nargs="*", metavar="TARGET",
                        help=f"Upload to every server, or just: {', '.join(target_names)}.")
    parser.add_argument("--pr", "--PR", action="store_true", dest="pr",
                        help="Open the RTX PR, or update it if it's still open.")
    parser.add_argument("--record", action="store_true", help="Commit and push the build record.")
    parser.add_argument("--full", action="store_true", help="--build --stage --pr --record, after asking.")
    parser.add_argument("--rehearse", action="store_true",
                        help="Show the plan and check access for the actions given; change nothing.")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="Don't ask. Risky choices still stop unless you've asked for them by flag.")
    parser.add_argument("--new-version", action="store_true",
                        help="With --build ARTIFACT on a staged build, create the next version without asking.")
    parser.add_argument("-c", "--cores", type=int, default=os.cpu_count() or 1,
                        help="Cores Snakemake may use (default: all).")
    parser.add_argument("--unlock", action="store_true", help="Clear a stale Snakemake lock and exit.")
    args = parser.parse_args(argv)

    if args.full and (args.build is not None or args.stage is not None or args.pr or args.record):
        parser.error("--full already includes --build, --stage, --pr and --record")
    args.build = None if args.build is None else [ALIASES.get(n, n) for n in args.build]
    for name in args.build or ():
        if name not in BY_NAME:
            parser.error(f"unknown artifact {name!r}; choose from {', '.join(artifacts)}")
    for name in args.stage or ():
        if name not in target_names:
            parser.error(f"unknown staging target {name!r}; choose from {', '.join(target_names)}")
    if args.new_version and not args.build:
        parser.error("--new-version goes with --build ARTIFACT")
    if args.full:
        args.build, args.stage, args.pr, args.record = [], [], True, True
    if args.build is None and args.stage is None and not args.pr and not args.record and not args.unlock:
        args.build, args.rehearse = [], True  # a bare `autobuild` works for anyone, with no server access
    return args


def main(argv: list[str] | None = None) -> None:
    config = local_config.load_config(REPO)
    args = parse_args(argv, list(config["staging"]["targets"]))
    if args.unlock:
        subprocess.run([sys.executable, "-m", "snakemake", "--unlock"], cwd=REPO, check=True)
        return
    if unset := local_config.missing_settings(config, keys=("graph_version",)):
        raise SystemExit(f"Missing settings: {', '.join(unset)}. {local_config.SETUP_HINT}")

    config["_cores"] = args.cores
    build = Build(config)
    print(f"Tier0 build {build.version}, in {build.dir}" + (" (rehearsal: nothing changes)" if args.rehearse else ""))

    if args.full and not args.rehearse and not args.yes:
        print("This builds and tests, uploads to " + ", ".join(build.targets) + ", opens or updates the "
              f"PR on {config['rtx_pr']['repo']}, and pushes the build record.")
        if not confirm("Go ahead?"):
            raise SystemExit("Cancelled; nothing was done.")

    steps = []
    if args.build is not None:
        steps.append(("build", lambda: do_build(build, args.build, args.rehearse, args.new_version, args.yes)))
    if args.stage is not None:
        steps.append(("stage", lambda: do_stage(build, args.stage, args.rehearse, args.yes)))
    if args.pr:
        steps.append(("PR", lambda: do_pr(build, args.rehearse)))
    if args.record:
        steps.append(("record", lambda: do_record(build, args.rehearse)))

    problems = []
    for name, step in steps:
        print(f"\n=== {name} ===", flush=True)
        try:
            step()
        except Fail as fail:
            if not args.rehearse:  # a rehearsal reports everything; a real run stops at the first
                raise SystemExit(f"STOPPED at {name}: {fail}") from None
            print(f"PROBLEM: {fail}")
            problems.append(name)
    if problems:
        raise SystemExit("Rehearsal found problems in: " + ", ".join(problems))
    if args.rehearse:
        print("\nRehearsal finished; nothing was changed.")
