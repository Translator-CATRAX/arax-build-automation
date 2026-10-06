"""The build's config_dbs.json, as RTXConfiguration and ARAX_database_manager read it."""

from pathlib import PurePosixPath

import pytest

# RTXConfiguration reads each of these with [...], so a missing one stops ARAX.
REQUIRED_KEYS = {"cohd_database", "curie_to_pmids", "curie_ngd", "kg2c_sqlite", "tier0_sqlite",
                 "fda_approved_drugs", "autocomplete", "explainable_dtd_db"}
BUILT_KEYS = {"curie_to_pmids", "curie_ngd", "kg2c_sqlite", "tier0_sqlite",
              "fda_approved_drugs", "autocomplete"}


@pytest.fixture(scope="module")
def downloads(config_dbs) -> dict[str, str]:
    return config_dbs["database_downloads"]


def test_required_keys(downloads):
    assert REQUIRED_KEYS <= downloads.keys()


def test_built_entries_point_at_this_builds_files(downloads, build_dir):
    paths = {key: PurePosixPath(downloads[key]) for key in BUILT_KEYS}
    # One release directory, named after the graph version.
    assert {p.parent.name for p in paths.values()} == {f"tier0-{build_dir.name}"}
    for key, path in paths.items():
        assert (build_dir / path.name).is_file(), f"{key}: {path.name} was not built"
        # ARAX_database_manager takes the version from after the last "_v".
        assert path.name.split("_v")[-1].split(".sqlite")[0], key
    assert downloads["tier0_sqlite"] == downloads["kg2c_sqlite"]


def test_built_entries_have_md5s(downloads, build_dir):
    # Deployment checks every database against its md5; one without an md5
    # fails the image build.
    for key in BUILT_KEYS:
        name = PurePosixPath(downloads[key]).name
        md5_file = build_dir / "md5_sums" / f"{name}.md5"
        assert md5_file.is_file(), f"{key}: no {md5_file.name}"
        assert md5_file.read_text().split()[1] == name, md5_file


def test_everything_else_is_carried_over(config_dbs, rtx_config_dbs):
    downloads = {k: v for k, v in config_dbs["database_downloads"].items() if k not in BUILT_KEYS}
    template = {k: v for k, v in rtx_config_dbs["database_downloads"].items() if k not in BUILT_KEYS}
    assert downloads == template
    assert {k: v for k, v in config_dbs.items() if k != "database_downloads"} == \
        {k: v for k, v in rtx_config_dbs.items() if k != "database_downloads"}


def test_no_sqlite_sidecar_files(build_dir):
    # ARAX opens databases with immutable=1 unless a -wal file exists; ship none.
    assert not list(build_dir.glob("*.sqlite-wal")) + list(build_dir.glob("*.sqlite-shm"))
