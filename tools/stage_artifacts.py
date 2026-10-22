#!/usr/bin/env python3
"""Stage a build's artifacts on the staging servers outside the pipeline.

For files made or remade outside a build run (e.g. an ExplainableDTD file
dropped into the build directory). By default every artifact in
{output_root}/YYYYMMDD/ (*.sqlite, *.db, *.pickle) goes to every target in
config.yaml's staging.targets; name files and/or targets to narrow it:

    python tools/stage_artifacts.py --tier0 20260928
    python tools/stage_artifacts.py --tier0 20260928 ExplainableDTD_v1.0-tier0-20260928-all_with_paths.db
    python tools/stage_artifacts.py --tier0 20260928 --targets cicd arax_ncats

Each artifact gets a md5sum-format md5_sums/<artifact>.md5 if it has none; an
artifact that no longer matches its existing .md5 stops the run (delete the
stale .md5 if the file was remade on purpose). On the ssh targets files are
uploaded into a hidden directory next to the release, md5-checked there, then
moved into {root}/tier0-YYYYMMDD/ and checked again. On the SFTP drop the
artifacts go to {databases}/tier0-YYYYMMDD/ and then their .md5 files to
{md5_sums}/tier0-YYYYMMDD/, checked by size (it has no shell).

Unlike the pipeline's stage step, the release directory may already exist:
files already there with the right md5 (or size, on SFTP) are skipped. A file
already there with different content stops that target; nothing is ever
overwritten or deleted. It shows the plan and asks before uploading.
"""

import argparse
import shlex
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "workflow" / "scripts" / "arax"))
import stage_databases as ssh_stage  # noqa: E402
import stage_sftp as sftp_stage  # noqa: E402

ARTIFACT_SUFFIXES = (".sqlite", ".db", ".pickle")
FREE_SPACE_MARGIN = ssh_stage.FREE_SPACE_MARGIN
SSH_OPTIONS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]
Stop = ssh_stage.Stop


def load_config() -> dict:
    import yaml
    return yaml.safe_load((REPO / "config" / "config.yaml").read_text())


def md5sum(path: Path) -> str:
    return ssh_stage.run("md5sum", str(path)).split()[0]


def resolve_artifacts(build_dir: Path, names: list[str]) -> list[Path]:
    md5_dir = build_dir / "md5_sums"
    if not names:
        listed = {f.read_text().split()[1] for f in md5_dir.glob("*.md5")}
        return sorted(p for p in build_dir.iterdir()
                      if p.is_file() and (p.suffix in ARTIFACT_SUFFIXES or p.name in listed))
    artifacts = []
    for name in names:
        path = build_dir / Path(name).name
        if not path.is_file():
            raise Stop(f"No artifact {path}")
        artifacts.append(path)
    return artifacts


def check_md5s(artifacts: list[Path], md5_dir: Path) -> dict[str, str]:
    """md5 of each artifact, checked against (or written to) md5_sums/."""
    md5_dir.mkdir(exist_ok=True)
    digests = {}
    for path in artifacts:
        md5_file = md5_dir / f"{path.name}.md5"
        print(f"md5 {path.name} ...", end=" ", flush=True)
        digest = md5sum(path)
        if md5_file.exists():
            recorded, name = md5_file.read_text().split()
            if name != path.name or recorded != digest:
                print("MISMATCH")
                raise Stop(f"{path.name} no longer matches {md5_file}; if the file was remade "
                           "on purpose, delete that .md5 and rerun")
            print("matches its .md5")
        else:
            md5_file.write_text(f"{digest}  {path.name}\n")
            print(f"wrote {md5_file.name}")
        digests[path.name] = digest
    return digests


# ------------------------------------------------------------------ ssh hosts

def plan_ssh(target: dict, release: str, artifacts: list[Path], digests: dict) -> list[Path]:
    host, root = target["host"], target["root"].rstrip("/")
    release_dir = f"{root}/{release}"
    exists = ssh_stage.ssh(host, f"test -d {shlex.quote(release_dir)} && echo yes || true").strip()
    staged = ssh_stage.remote_md5s(host, release_dir, list(digests)) if exists else {}
    differing = [n for n in staged if staged[n] != digests[n]]
    if differing:
        raise Stop(f"{host}:{release_dir} already has different copies of: {', '.join(differing)}; "
                   "they are never overwritten")
    todo = [p for p in artifacts if p.name not in staged]
    needed = sum(p.stat().st_size for p in todo)
    if todo:
        free = int(ssh_stage.ssh(host, f"df --output=avail -B1 {shlex.quote(root)} | tail -1"))
        if free < needed + FREE_SPACE_MARGIN:
            raise Stop(f"Only {free / 1024**3:.0f} GB free on {host}; "
                       f"{needed / 1024**3:.1f} GB needed plus a margin")
    return todo


def stage_ssh(target: dict, release: str, todo: list[Path], digests: dict) -> None:
    host, root = target["host"], target["root"].rstrip("/")
    release_dir, staging_dir = f"{root}/{release}", f"{root}/.staging-{release}-files"
    sudo = "sudo -n " if target.get("sudo") else ""
    mode = shlex.quote(target.get("dir_mode", "775"))
    for directory in (release_dir, staging_dir):
        quoted = shlex.quote(directory)
        ssh_stage.ssh(host, f"test -d {quoted} || ({sudo}mkdir -p {quoted}"
                            f" && {sudo}chown \"$(id -u):$(id -g)\" {quoted} && chmod {mode} {quoted})")
    names = [p.name for p in todo]
    if ssh_stage.ssh(host, "command -v rsync || true").strip():
        # Not captured, so rsync's progress shows; an interrupted file resumes on rerun.
        subprocess.run(["rsync", "--partial", "--times", "--chmod=F644", "--info=progress2",
                        "-e", shlex.join(["ssh", *SSH_OPTIONS]), *map(str, todo),
                        f"{host}:{staging_dir}/"], check=True)
    else:
        ssh_stage.run("scp", "-p", "-q", *SSH_OPTIONS, *map(str, todo), f"{host}:{staging_dir}/")
        ssh_stage.ssh(host, "chmod 644 " + " ".join(shlex.quote(f"{staging_dir}/{n}") for n in names))
    expected = {n: digests[n] for n in names}
    if ssh_stage.remote_md5s(host, staging_dir, names) != expected:
        raise Stop(f"md5 mismatch after upload in {host}:{staging_dir}")
    # mv -n never replaces a file that appeared in the release meanwhile.
    for name in names:
        ssh_stage.ssh(host, f"{sudo}mv -n {shlex.quote(f'{staging_dir}/{name}')} "
                            f"{shlex.quote(f'{release_dir}/{name}')}")
    if ssh_stage.remote_md5s(host, release_dir, names) != expected:
        raise Stop(f"md5 mismatch in {host}:{release_dir} after moving the files in")
    ssh_stage.ssh(host, f"rmdir {shlex.quote(staging_dir)} 2>/dev/null || true")
    if target.get("read_check"):
        ssh_stage.check_readable(host, target["read_check"], release_dir, names)


# ------------------------------------------------------------------ SFTP drop

def sftp_dirs(target: dict, release: str) -> tuple[str, str]:
    return (f"{target.get('databases', 'databases').rstrip('/')}/{release}",
            f"{target.get('md5_sums', 'md5_sums').rstrip('/')}/{release}")


def plan_sftp(target: dict, release: str, artifacts: list[Path], md5_dir: Path) -> list[tuple[Path, str]]:
    data_dir, md5s_dir = sftp_dirs(target, release)
    wanted = ([(p, data_dir) for p in artifacts]
              + [(md5_dir / f"{p.name}.md5", md5s_dir) for p in artifacts])
    todo = []
    for directory in (data_dir, md5s_dir):
        present = sftp_stage.remote_sizes(target["host"], directory) or {}
        for path, d in wanted:
            if d != directory:
                continue
            if path.name not in present:
                todo.append((path, d))
            elif present[path.name] != path.stat().st_size:
                raise Stop(f"{target['host']}:{d}/{path.name} already exists at a different size; "
                           "it is never overwritten")
    return todo


def stage_sftp(target: dict, release: str, todo: list[tuple[Path, str]]) -> None:
    host = target["host"]
    data_dir, md5s_dir = sftp_dirs(target, release)
    # Artifacts first, then their md5 files: a release whose .md5 files are there is complete.
    todo = sorted(todo, key=lambda item: item[1] == md5s_dir)
    commands = [f"-mkdir {data_dir}", f"-mkdir {md5s_dir}"]
    commands += [f'put "{path}" "{d}/{path.name}"' for path, d in todo]
    result = sftp_stage.sftp(host, commands)
    if result.returncode != 0:
        raise Stop(f"upload to {host} failed: {result.stderr.strip()}")
    for directory in (data_dir, md5s_dir):
        present = sftp_stage.remote_sizes(host, directory) or {}
        wrong = [p.name for p, d in todo if d == directory and present.get(p.name) != p.stat().st_size]
        if wrong:
            raise Stop(f"{host}:{directory} is missing or has the wrong size for: {', '.join(wrong)}")


# ----------------------------------------------------------------------- main

def main() -> None:
    config = load_config()
    targets = config["staging"]["targets"]
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tier0", required=True, help="Build version, YYYYMMDD (or tier0-YYYYMMDD).")
    parser.add_argument("files", nargs="*",
                        help="Artifacts in the build directory to stage (default: all of them).")
    parser.add_argument("--targets", nargs="+", choices=list(targets), default=list(targets),
                        help="Staging targets from config.yaml (default: all).")
    parser.add_argument("--dry-run", action="store_true", help="Show the plan; upload nothing.")
    parser.add_argument("-y", "--yes", action="store_true", help="Do not ask before uploading.")
    args = parser.parse_args()

    version = args.tier0.removeprefix("tier0-")
    release = f"tier0-{version}"
    build_dir = Path(config["output_root"]) / version
    md5_dir = build_dir / "md5_sums"
    try:
        if not build_dir.is_dir():
            raise Stop(f"No build directory {build_dir}")
        artifacts = resolve_artifacts(build_dir, args.files)
        if not artifacts:
            raise Stop(f"No artifacts in {build_dir}")
        print(f"{len(artifacts)} artifacts from {build_dir}:")
        for path in artifacts:
            print(f"  {path.name} ({path.stat().st_size / 1024**3:.2f} GB)")
        digests = check_md5s(artifacts, md5_dir)

        plans, problems = {}, {}
        for name in args.targets:
            target = targets[name]
            try:
                if target.get("sftp"):
                    plans[name] = plan_sftp(target, release, artifacts, md5_dir)
                else:
                    plans[name] = plan_ssh(target, release, artifacts, digests)
            except Stop as stop:
                problems[name] = str(stop)

        print()
        for name, todo in plans.items():
            files = [item[0] if isinstance(item, tuple) else item for item in todo]
            where = targets[name]["host"]
            if not files:
                print(f"{name} ({where}): already staged, nothing to upload")
                continue
            size = sum(f.stat().st_size for f in files) / 1024**3
            print(f"{name} ({where}): upload {len(files)} files, {size:.2f} GB")
            for f in files:
                print(f"    {f.name}")
        for name, problem in problems.items():
            print(f"{name}: CANNOT STAGE: {problem}")

        pending = {n: t for n, t in plans.items() if t}
        if args.dry_run or not pending:
            print("\nDry run; nothing uploaded." if args.dry_run else "\nNothing to upload.")
            raise SystemExit(1 if problems else 0)
        if not args.yes and input("\nUpload? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Nothing uploaded.")
            return

        failed = dict(problems)
        for name, todo in pending.items():
            target = targets[name]
            print(f"\nStaging on {name} ({target['host']}) ...", flush=True)
            try:
                if target.get("sftp"):
                    stage_sftp(target, release, todo)
                else:
                    stage_ssh(target, release, todo, digests)
                print(f"{name}: staged and verified")
            except (Stop, subprocess.SubprocessError) as stop:
                failed[name] = str(stop)
                print(f"{name}: FAILED: {stop}", file=sys.stderr)
        if failed:
            print("\nNot staged on: " + ", ".join(failed), file=sys.stderr)
            raise SystemExit(1)
        print("\nAll targets staged.")
    except Stop as stop:
        print(f"STOPPED: {stop}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
