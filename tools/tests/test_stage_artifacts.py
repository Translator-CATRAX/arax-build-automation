"""SSH staging against a local directory standing in for the server: commands meant for
the server run in a local shell, and scp copies locally. Run with:

    .venv/bin/python -m pytest tools/tests -p no:cacheprovider
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "tools")]

import stage_artifacts as stage  # noqa: E402

RELEASE = "tier0-20261018"
HOST = "h"


@pytest.fixture
def server(tmp_path, monkeypatch):
    root = tmp_path / "server"
    root.mkdir()

    def ssh(host, command):
        if command == "command -v rsync || true":
            return ""  # take the scp path, which the fake handles
        result = subprocess.run(["bash", "-c", command], text=True, capture_output=True, check=False)
        if result.returncode != 0:
            raise stage.Stop(result.stderr)
        return result.stdout

    real_run = stage.ssh_stage.run

    def run(*args):
        if args[0] == "scp":
            *sources, destination = [a for a in args[1:] if not a.startswith("-") and "=" not in a]
            for source in sources:
                shutil.copy(source, destination.split(":", 1)[1])
            return ""
        return real_run(*args)

    monkeypatch.setattr(stage.ssh_stage, "ssh", ssh)
    monkeypatch.setattr(stage.ssh_stage, "run", run)
    return root


@pytest.fixture
def build(tmp_path):
    directory = tmp_path / "build"
    directory.mkdir()
    for name in ("a.sqlite", "b.sqlite"):
        (directory / name).write_text(f"contents of {name}\n")
    artifacts = sorted(p for p in directory.iterdir() if p.is_file())
    digests = stage.check_md5s(artifacts, directory / "md5_sums")
    return directory, artifacts, digests


def stage_all(server, build):
    directory, artifacts, digests = build
    target = {"host": HOST, "root": str(server)}
    todo = stage.plan_ssh(target, RELEASE, artifacts, digests, directory / "md5_sums")
    if todo:
        stage.stage_ssh(target, RELEASE, todo, digests, directory / "md5_sums")
    return todo


def test_md5_files_land_in_the_release_and_check_out(server, build):
    stage_all(server, build)
    release = server / RELEASE
    assert sorted(p.name for p in (release / "md5_sums").iterdir()) == ["a.sqlite.md5", "b.sqlite.md5"]
    subprocess.run(["md5sum", "-c", "md5_sums/a.sqlite.md5", "md5_sums/b.sqlite.md5"],
                   cwd=release, check=True, capture_output=True)
    assert not (server / f".staging-{RELEASE}-files").exists()


def test_a_second_run_has_nothing_to_do(server, build):
    stage_all(server, build)
    assert stage_all(server, build) == []


def test_an_already_staged_release_gets_only_its_md5_files(server, build):
    directory, artifacts, _ = build
    release = server / RELEASE
    release.mkdir()
    for path in artifacts:
        shutil.copy(path, release)
    todo = stage_all(server, build)
    assert all(p.parent == directory / "md5_sums" for p in todo)
    assert (release / "md5_sums" / "a.sqlite.md5").read_text() == \
        (directory / "md5_sums" / "a.sqlite.md5").read_text()


def test_a_different_md5_file_on_the_server_stops_and_is_kept(server, build):
    md5s = server / RELEASE / "md5_sums"
    md5s.mkdir(parents=True)
    (md5s / "a.sqlite.md5").write_text("0" * 32 + "  a.sqlite\n")
    with pytest.raises(stage.Stop, match="a.sqlite.md5"):
        stage_all(server, build)
    assert (md5s / "a.sqlite.md5").read_text() == "0" * 32 + "  a.sqlite\n"


def test_one_space_md5_file_counts_as_the_same(server, build):
    directory, _, digests = build
    md5s = server / RELEASE / "md5_sums"
    md5s.mkdir(parents=True)
    (md5s / "a.sqlite.md5").write_text(f"{digests['a.sqlite']} a.sqlite")
    todo = stage_all(server, build)
    assert directory / "md5_sums" / "a.sqlite.md5" not in todo
