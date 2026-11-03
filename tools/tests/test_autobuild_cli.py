"""The wrapper's own logic, with no servers or data. Run with:

    .venv/bin/python -m pytest tools/tests -p no:cacheprovider
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "tools"), str(REPO / "workflow" / "scripts" / "arax"),
                str(REPO / "workflow" / "scripts" / "sources")]

import autobuild_cli as cli  # noqa: E402
import open_config_pr as pr  # noqa: E402
import tier0  # noqa: E402

BRANCH = "autobuild/tier0-20261018"
TARGETS = {"arax_databases": {"host": "h", "root": "/r"},
           "itrb_sftp": {"host": "team-expander-someone@sftp.example", "sftp": True, "databases": "databases"}}


def a_pr(state, head=BRANCH, number=1, created="2026-10-01"):
    return {"state": state, "headRefName": head, "url": f"https://pr/{number}", "createdAt": created}


@pytest.fixture
def build(tmp_path):
    config = {"graph_version": "20261018", "output_root": str(tmp_path), "staging": {"targets": TARGETS}}
    built = cli.Build(config)
    built.dir.mkdir()
    return built


def make_tested(directory: Path, name: str) -> Path:
    """A file that passed its tests: artifact, then results, then md5."""
    (directory / "test_results").mkdir(exist_ok=True)
    (directory / "md5_sums").mkdir(exist_ok=True)
    path, results, md5 = (directory / name, directory / "test_results" / "artifacts.xml",
                          directory / "md5_sums" / f"{name}.md5")
    for number, item in enumerate((path, results, md5)):
        item.write_text("x")
        os.utime(item, (1000 + number, 1000 + number))
    return path


class TestPlan:
    def test_no_prs_opens_new(self):
        assert pr.plan([], BRANCH) == ("new", None)

    def test_open_pr_is_updated(self):
        assert pr.plan([a_pr("OPEN")], BRANCH)[0] == "update"

    def test_merged_pr_opens_new(self):
        assert pr.plan([a_pr("MERGED")], BRANCH)[0] == "after merge"

    def test_closed_pr_opens_new(self):
        assert pr.plan([a_pr("CLOSED")], BRANCH) == ("new", None)

    def test_open_wins_over_merged_and_closed(self):
        prs = [a_pr("MERGED", number=3), a_pr("OPEN", f"{BRANCH}-update-1", 4), a_pr("CLOSED", number=2)]
        action, existing = pr.plan(prs, BRANCH)
        assert action == "update" and existing["headRefName"] == f"{BRANCH}-update-1"

    def test_real_shape_of_closed_then_merged(self):
        # 2905 was closed and 2906 merged, on the same branch.
        assert pr.plan([a_pr("MERGED", number=2906), a_pr("CLOSED", number=2905)], BRANCH)[0] == "after merge"

    def test_another_builds_open_pr_stops(self):
        with pytest.raises(pr.Stop):
            pr.plan([a_pr("OPEN", "autobuild/tier0-20261101")], BRANCH)

    def test_another_builds_merged_pr_is_ignored(self):
        assert pr.plan([a_pr("MERGED", "autobuild/tier0-20260928")], BRANCH) == ("new", None)

    def test_similar_branch_name_is_not_this_build(self):
        assert not pr.is_this_build(BRANCH, f"{BRANCH}9")


class TestBranchName:
    def test_first_pr_uses_the_plain_name(self):
        assert pr.new_branch_name(BRANCH, [], set(), True) == BRANCH

    def test_closed_pr_with_branch_left_gets_the_next_name(self):
        assert pr.new_branch_name(BRANCH, [a_pr("CLOSED")], {BRANCH}, True) == f"{BRANCH}-update-1"

    def test_after_merge_never_reuses_the_name(self):
        assert pr.new_branch_name(BRANCH, [], set(), False) == f"{BRANCH}-update-1"

    def test_skips_used_names(self):
        used = {BRANCH, f"{BRANCH}-update-1", f"{BRANCH}-update-2"}
        assert pr.new_branch_name(BRANCH, [], used, False) == f"{BRANCH}-update-3"


class TestApplyEntries:
    def downloads(self, value):
        return {"database_downloads": {key: value for key in pr.BUILT_KEYS}}

    def test_keeps_everything_else_on_the_branch(self):
        target = self.downloads("old")
        target["database_downloads"]["explainable_dtd_db"] = "from the branch"
        target["neo4j"] = {"KG2c": "x"}
        merged = pr.apply_built_entries(target, self.downloads("new"))
        assert merged["database_downloads"]["curie_ngd"] == "new"
        assert merged["database_downloads"]["explainable_dtd_db"] == "from the branch"
        assert merged["neo4j"] == {"KG2c": "x"}

    def test_a_renamed_key_stops(self):
        target = self.downloads("old")
        del target["database_downloads"]["tier0_sqlite"]
        with pytest.raises(pr.Stop):
            pr.apply_built_entries(target, self.downloads("new"))

    def test_release_of(self):
        path = "/x/tier0-20261018/tier0-info-for-overlay_v1.0_tier0-20261018.sqlite"
        assert pr.release_of({"database_downloads": {"tier0_sqlite": path}}) == "20261018"


class TestVersions:
    def test_file_version(self):
        ngd = cli.BY_NAME["curie_ngd"]
        assert cli.file_version(ngd, "curie_ngd_v1.2_tier0-20261018.sqlite", "tier0-20261018") == "1.2"
        assert cli.file_version(ngd, "curie_ngd_v1.2_tier0-20260928.sqlite", "tier0-20261018") is None
        assert cli.file_version(cli.BY_NAME["fda_approved_drugs"], "fda_approved_drugs_v1.1.pickle", "x") == "1.1"

    def test_next_version_uses_servers_too(self, build, monkeypatch):
        monkeypatch.setattr(cli, "remote_listing", lambda *_: ["curie_ngd_v1.4_tier0-20261018.sqlite"])
        assert build.next_version() == "1.5"

    def test_next_version_starts_at_one_one(self, build, monkeypatch):
        monkeypatch.setattr(cli, "remote_listing", lambda *_: [])
        assert build.next_version() == "1.1"

    def test_next_version_counts_a_failed_rebuilds_directory(self, build, monkeypatch):
        monkeypatch.setattr(cli, "remote_listing", lambda *_: [])
        (build.dir / "v1.3").mkdir()
        assert build.next_version() == "1.4"

    def test_current_prefers_the_newest_real_file(self, build):
        ngd = cli.BY_NAME["curie_ngd"]
        for version in ("v1.2", "v1.10"):
            (build.dir / version).mkdir()
            (build.dir / version / build.filename(ngd, version[1:])).write_text("x")
        assert build.current(ngd).parent.name == "v1.10"

    def test_current_ignores_a_test_link(self, build):
        pmids = cli.BY_NAME["curie_to_pmids"]
        base = build.dir / build.filename(pmids, "1.0")
        base.write_text("x")
        (build.dir / "v1.1").mkdir()
        (build.dir / "v1.1" / base.name).symlink_to(base)
        assert build.current(pmids) == base

    def test_dependents(self):
        assert cli.with_dependents(["curie_to_pmids"]) == ["curie_to_pmids", "curie_ngd"]
        assert cli.with_dependents(["curie_ngd"]) == ["curie_ngd"]


class TestTested:
    def test_tested_file(self, build):
        assert build.is_tested(make_tested(build.dir, "curie_ngd_v1.0_tier0-20261018.sqlite"))

    def test_rebuilt_after_its_tests_is_not_tested(self, build):
        path = make_tested(build.dir, "curie_ngd_v1.0_tier0-20261018.sqlite")
        os.utime(path, (2000, 2000))
        assert not build.is_tested(path)

    def test_no_md5_is_not_tested(self, build):
        path = make_tested(build.dir, "curie_ngd_v1.0_tier0-20261018.sqlite")
        (build.dir / "md5_sums" / f"{path.name}.md5").unlink()
        assert not build.is_tested(path)


class TestStageChoices:
    @pytest.fixture
    def staged(self, build, monkeypatch):
        calls = []
        monkeypatch.setattr(cli, "stage_files", lambda b, target, rehearse: (calls.append(target), (True, True))[1])
        return calls

    def test_cancel_stages_nothing(self, build, staged, monkeypatch):
        monkeypatch.setattr(cli, "choose", lambda *_: "c")
        with pytest.raises(cli.Fail):
            cli.do_stage(build, [], False, False)
        assert not staged

    def test_stage_the_rest_writes_no_complete_result(self, build, staged, monkeypatch):
        monkeypatch.setattr(cli, "choose", lambda *_: "s")
        cli.do_stage(build, [], False, False)
        assert staged == list(TARGETS)
        assert not build.stage_result("arax_databases").exists()

    def test_yes_stages_the_rest(self, build, staged):
        cli.do_stage(build, ["arax_databases"], False, True)
        assert staged == ["arax_databases"]

    def test_complete_release_writes_results_without_logins(self, build, staged, monkeypatch):
        monkeypatch.setattr(build, "missing", lambda: [])
        cli.do_stage(build, [], False, False)
        result = json.loads(build.stage_result("itrb_sftp").read_text())
        assert result["staged"] is True and "someone" not in result["location"]

    def test_missing_host_stops(self, build, staged):
        build.targets["itrb_sftp"]["host"] = "team-expander-USERNAME@sftp"
        with pytest.raises(cli.Fail):
            cli.do_stage(build, ["itrb_sftp"], False, True)
        build.targets["itrb_sftp"]["host"] = "team-expander-someone@sftp.example"

    def test_rehearsal_writes_nothing(self, build, staged, monkeypatch):
        monkeypatch.setattr(build, "missing", lambda: [])
        cli.do_stage(build, [], True, False)
        assert not (build.dir / "staged").exists()


class TestStageCommand:
    def test_the_real_tool_accepts_what_it_is_given(self, build, monkeypatch):
        # The tool's --targets takes several values, so filenames must not follow it.
        path = make_tested(build.dir, build.filename(cli.BY_NAME["curie_ngd"], "1.0"))
        seen = []
        monkeypatch.setattr(cli, "run_logged", lambda command, log=None, check=True: (seen.append(command), (0, ""))[1])
        cli.stage_files(build, "arax_databases", True)
        command = list(map(str, seen[0]))
        assert command[command.index("--targets") + 1:] == ["arax_databases", "--dry-run"]
        assert path.name in command[:command.index("--targets")]


class TestBuildGuards:
    def released(self, build):
        (build.dir / "rtx_pr.json").write_text("{}")

    def test_plain_build_of_a_released_build_does_nothing(self, build, monkeypatch):
        self.released(build)
        monkeypatch.setattr(cli, "run_snakemake", lambda *_: pytest.fail("must not run"))
        cli.do_build(build, [], False, False, False)

    def test_declining_a_new_version_builds_nothing(self, build, monkeypatch):
        self.released(build)
        monkeypatch.setattr(cli, "remote_listing", lambda *_: [])
        monkeypatch.setattr(cli, "confirm", lambda *_: False)
        with pytest.raises(cli.Fail):
            cli.do_build(build, ["curie_ngd"], False, False, False)
        assert not (build.dir / "v1.1").exists()

    def test_yes_alone_does_not_create_a_version(self, build, monkeypatch):
        self.released(build)
        monkeypatch.setattr(cli, "remote_listing", lambda *_: [])
        with pytest.raises(cli.Fail, match="--new-version"):
            cli.do_build(build, ["curie_ngd"], False, False, True)

    def test_rehearsal_creates_nothing(self, build, monkeypatch):
        self.released(build)
        monkeypatch.setattr(cli, "remote_listing", lambda *_: [])
        cli.do_build(build, ["curie_ngd"], True, False, False)
        assert not (build.dir / "v1.1").exists()

    def test_named_artifact_before_release_forces_its_rule(self, build, monkeypatch):
        build.config["_cores"] = 4
        calls = []
        monkeypatch.setattr(cli, "run_snakemake", lambda *args: calls.append(args))
        cli.do_build(build, ["fda_approved_drugs"], False, False, False)
        assert "fda_drugs" in calls[0] and "--forcerun" in calls[0] and "-n" not in calls[0]


class TestRecord:
    def test_needs_real_staging_and_pr(self, build):
        assert len(cli.record_problems(build)) == len(TARGETS) + 1

    def test_ready(self, build):
        (build.dir / "staged").mkdir()
        for target in TARGETS:
            build.stage_result(target).write_text(json.dumps({"staged": True}))
        (build.dir / "rtx_pr.json").write_text(json.dumps({"url": "u"}))
        assert not cli.record_problems(build)


class TestArgs:
    def parse(self, *argv):
        return cli.parse_args(list(argv), list(TARGETS))

    def test_bare_is_a_rehearsed_build(self):
        args = self.parse()
        assert args.rehearse and args.build == [] and args.stage is None and not args.pr

    def test_only_flagged_actions(self):
        args = self.parse("--stage", "itrb_sftp")
        assert args.stage == ["itrb_sftp"] and args.build is None and not args.pr and not args.record

    def test_build_and_stage_with_names(self):
        args = self.parse("--build", "curie_ngd", "overlay", "--stage", "arax_databases")
        assert args.build == ["curie_ngd", "tier0-info-for-overlay"] and args.stage == ["arax_databases"]

    def test_full_is_everything(self):
        args = self.parse("--full")
        assert args.build == [] and args.stage == [] and args.pr and args.record and not args.rehearse

    def test_pr_spelled_either_way(self):
        assert self.parse("--PR").pr and self.parse("--pr").pr

    @pytest.mark.parametrize("argv", [["--build", "nope"], ["--stage", "nope"], ["--full", "--pr"],
                                      ["--new-version"], ["-n"]])
    def test_rejected(self, argv):
        with pytest.raises(SystemExit):
            self.parse(*argv)


def complete_graph(root: Path, version: str) -> None:
    (root / version).mkdir(parents=True)
    for name in tier0.GRAPH_FILES:
        (root / version / name).write_text("{}")


LATEST = tier0.Release("1.0.4", "20261018", "https://x/1.0.4/")
OLD = tier0.Release("1.0.2", "20260928", "https://x/1.0.2/")


class TestChooseGraph:
    def test_unset_uses_latest_and_fetches(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tier0, "latest_release", lambda _: LATEST)
        assert tier0.resolve(tmp_path, None, "https://x") == ("20261018", LATEST)

    def test_unset_uses_a_local_latest(self, tmp_path, monkeypatch):
        complete_graph(tmp_path, "20261018")
        monkeypatch.setattr(tier0, "latest_release", lambda _: LATEST)
        assert tier0.resolve(tmp_path, None, "https://x") == ("20261018", None)

    def test_unset_offline_falls_back_to_newest_local(self, tmp_path, monkeypatch):
        complete_graph(tmp_path, "20260621")
        complete_graph(tmp_path, "20260928")
        def offline(_):
            raise OSError("no network")
        monkeypatch.setattr(tier0, "latest_release", offline)
        assert tier0.resolve(tmp_path, None, "https://x") == ("20260928", None)

    def test_unset_offline_with_nothing_local_stops(self, tmp_path, monkeypatch):
        def offline(_):
            raise OSError("no network")
        monkeypatch.setattr(tier0, "latest_release", offline)
        with pytest.raises(OSError):
            tier0.resolve(tmp_path, None, "https://x")

    def test_pinned_local_needs_no_network(self, tmp_path, monkeypatch):
        complete_graph(tmp_path, "20260928")
        monkeypatch.setattr(tier0, "_get", lambda _: pytest.fail("no network needed"))
        assert tier0.resolve(tmp_path, 20260928, "https://x") == ("20260928", None)

    def test_pinned_missing_is_found_by_date(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tier0, "find_release", lambda url, version: OLD)
        assert tier0.resolve(tmp_path, "20260928", "https://x") == ("20260928", OLD)

    def test_placeholder_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="YYYYMMDD"):
            tier0.resolve(tmp_path, "YYYYMMDD", "https://x")

    def test_incomplete_local_is_not_used(self, tmp_path, monkeypatch):
        (tmp_path / "20261018").mkdir()
        monkeypatch.setattr(tier0, "latest_release", lambda _: LATEST)
        assert tier0.resolve(tmp_path, None, "https://x")[1] == LATEST

    def test_release_folders_newest_first(self):
        html = """<a href="/r/1.0.2/"><a href="/r/1.0.10/"><a href="/r/latest/"><a href="/docs"><a href="/r/2026_06_21/">"""
        folders = tier0._release_folders(html)
        assert folders.index("1.0.10") < folders.index("1.0.2") and "latest" not in folders

    def test_release_date_comes_from_build_date(self):
        metadata = {"build_date": "2026-10-18T03:31:53Z"}
        assert tier0._release_from_metadata(metadata, "1.0.4", "u").version == "20261018"


class TestChooseVersion:
    def config(self, tmp_path, **extra):
        return {"output_root": str(tmp_path / "out"), "graph_root": str(tmp_path / "g"),
                "graph_releases": "https://x", **extra}

    def built(self, tmp_path, version):
        (tmp_path / "out" / version).mkdir(parents=True)
        (tmp_path / "out" / version / "build_report.md").write_text("x")

    def test_release_only_stays_on_the_newest_build(self, tmp_path, monkeypatch):
        self.built(tmp_path, "20260928")
        monkeypatch.setattr(tier0, "latest_release", lambda _: LATEST)
        assert cli.choose_version(self.config(tmp_path), builds=False) == "20260928"

    def test_build_moves_to_latest(self, tmp_path, monkeypatch, capsys):
        self.built(tmp_path, "20260928")
        monkeypatch.setattr(tier0, "latest_release", lambda _: LATEST)
        assert cli.choose_version(self.config(tmp_path), builds=True) == "20261018"
        assert "new build" in capsys.readouterr().out

    def test_pin_always_wins(self, tmp_path, monkeypatch):
        self.built(tmp_path, "20261018")
        monkeypatch.setattr(tier0, "find_release", lambda url, version: OLD)
        assert cli.choose_version(self.config(tmp_path, graph_version="20260928"), builds=False) == "20260928"


class TestFetch:
    @pytest.fixture
    def release(self, tmp_path):
        """A real archive, served over file://."""
        source = tmp_path / "src"
        source.mkdir()
        for name in tier0.GRAPH_FILES:
            (source / name).write_text(f"contents of {name}")
        served = tmp_path / "served"
        served.mkdir()
        subprocess.run(["tar", "--zstd", "-cf", str(served / tier0.ARCHIVE), "-C", str(source),
                        *tier0.GRAPH_FILES], check=True)
        return served

    def test_downloads_and_unpacks(self, release, tmp_path):
        dest = tmp_path / "g" / "20261018"
        tier0.fetch(f"file://{release}", dest)
        assert sorted(p.name for p in dest.iterdir()) == sorted(tier0.GRAPH_FILES)
        assert not list((tmp_path / "g").glob(".*"))

    def test_never_overwrites(self, release, tmp_path):
        dest = tmp_path / "g" / "20261018"
        dest.mkdir(parents=True)
        (dest / "nodes.jsonl").write_text("mine")
        with pytest.raises(RuntimeError, match="already has"):
            tier0.fetch(f"file://{release}", dest)
        assert (dest / "nodes.jsonl").read_text() == "mine"

    def test_damaged_archive_is_deleted(self, release, tmp_path):
        (release / tier0.ARCHIVE).write_bytes(b"not an archive")
        dest = tmp_path / "g" / "20261018"
        with pytest.raises(RuntimeError, match="Couldn't unpack"):
            tier0.fetch(f"file://{release}", dest)
        assert not (dest / "nodes.jsonl").exists()
        assert not (dest.with_name(".20261018.download") / tier0.ARCHIVE).exists()

    def test_not_enough_disk(self, release, tmp_path, monkeypatch):
        monkeypatch.setattr(tier0.shutil, "disk_usage", lambda _: type("U", (), {"free": 0})())
        with pytest.raises(RuntimeError, match="GiB free"):
            tier0.fetch(f"file://{release}", tmp_path / "g" / "20261018")
