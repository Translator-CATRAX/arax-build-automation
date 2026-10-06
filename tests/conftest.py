"""Fixtures that locate one build's artifacts.

Run against a build directory, e.g.

    pytest tests --build-dir /path/to/database/20260928 \
        --graph-dir /path/to/translator_kg/20260928 \
        --babel /path/to/babel-20260722.sqlite \
        --biolink-cache-dir /path/to/database/.cache/biolink \
        --rtx-config-dbs /path/to/RTX/code/config_dbs.json

The pipeline's test_artifacts rule does this after every build.
"""

import json
import pickle
import random
import sqlite3
from pathlib import Path

import numpy as np
import pytest


def pytest_addoption(parser):
    parser.addoption("--build-dir", type=Path, required=True,
                     help="Build output directory holding the artifacts (YYYYMMDD).")
    parser.addoption("--graph-dir", type=Path,
                     help="Tier0 graph directory the build used (nodes.jsonl, edges.jsonl).")
    parser.addoption("--babel", type=Path,
                     help="Babel sqlite the build used (the graph's babelVersion).")
    parser.addoption("--biolink-cache-dir", type=Path,
                     help="BiolinkHelper cache the build used, so tests need no download.")
    parser.addoption("--rtx-config-dbs", type=Path,
                     help="The RTX config_dbs.json the build's config_dbs.json was made "
                          "from; needed only by test_config_dbs.py.")


@pytest.fixture(scope="session")
def build_dir(request) -> Path:
    return request.config.getoption("--build-dir")


@pytest.fixture(scope="session")
def rtx_config_dbs(request) -> dict:
    return json.loads(_required_option(request, "--rtx-config-dbs").read_text())


def _required_option(request, name: str) -> Path:
    path = request.config.getoption(name)
    if path is None:
        pytest.fail(f"{name} is required for this test")
    return path


@pytest.fixture(scope="session")
def graph_dir(request) -> Path:
    return _required_option(request, "--graph-dir")


@pytest.fixture(scope="session")
def graph_node_ids(graph_dir) -> set[str]:
    """Every node ID in the graph's nodes.jsonl."""
    with open(graph_dir / "nodes.jsonl", encoding="utf-8") as f:
        return {json.loads(line)["id"] for line in f}


@pytest.fixture(scope="session")
def biolink_helper(request, graph_dir):
    """BiolinkHelper at the Biolink version the graph was built with."""
    from biolink_helper_pkg import BiolinkHelper
    metadata = json.loads((graph_dir / "graph-metadata.json").read_text())
    version = metadata.get("translator:biolinkVersion") or metadata["biolinkVersion"]
    return BiolinkHelper(version, str(_required_option(request, "--biolink-cache-dir")))


@pytest.fixture(scope="session")
def babel(request):
    from stitch.local_babel import connect_to_db_read_only
    with connect_to_db_read_only(str(_required_option(request, "--babel"))) as conn:
        yield conn


@pytest.fixture(scope="session")
def is_babel_preferred(babel):
    """Whether a CURIE is the preferred ID of one of its Babel cliques. Babel
    puts some CURIEs in several cliques (e.g. a UMLS ID in both a chemical and
    a protein clique); the builders keep each clique's preferred ID."""
    from stitch.local_babel import map_curie_to_preferred_curies
    def check(curie: str) -> bool:
        return any(row[0] == curie for row in map_curie_to_preferred_curies(babel, curie))
    return check


def _artifact(build_dir: Path, pattern: str) -> Path:
    matches = sorted(build_dir.glob(pattern))
    assert len(matches) == 1, f"expected one {pattern} in {build_dir}, found {matches}"
    return matches[0]


def _connect(path: Path):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def autocomplete_db(build_dir):
    yield from _connect(_artifact(build_dir, "autocomplete_v*_tier0-*.sqlite"))


@pytest.fixture(scope="session")
def overlay_db(build_dir):
    yield from _connect(_artifact(build_dir, "tier0-info-for-overlay_v*_tier0-*.sqlite"))


@pytest.fixture(scope="session")
def curie_to_pmids_db(build_dir):
    yield from _connect(_artifact(build_dir, "curie_to_pmids_v*_tier0-*.sqlite"))


@pytest.fixture(scope="session")
def curie_ngd_db(build_dir):
    yield from _connect(_artifact(build_dir, "curie_ngd_v*_tier0-*.sqlite"))


@pytest.fixture(scope="session")
def fda_approved_drugs(build_dir) -> set:
    with open(_artifact(build_dir, "fda_approved_drugs_v*.pickle"), "rb") as f:
        return pickle.load(f)


@pytest.fixture(scope="session")
def config_dbs(build_dir) -> dict:
    return json.loads((build_dir / "config_dbs.json").read_text())


def sample_rows(conn: sqlite3.Connection, table: str, columns: str, n: int = 2000,
                seed: int = 0) -> list[tuple]:
    """A reproducible random sample of up to n rows, picked by rowid so that
    sampling a large table does not scan it."""
    max_rowid = conn.execute(f"SELECT max(rowid) FROM {table}").fetchone()[0] or 0
    rowids = random.Random(seed).sample(range(1, max_rowid + 1), min(n, max_rowid))
    return conn.execute(f"SELECT {columns} FROM {table} WHERE rowid IN "
                        f"({','.join(map(str, rowids))})").fetchall()


@pytest.fixture(scope="session")
def pmids_of(curie_to_pmids_db):
    """Look up a CURIE's PMID array in curie_to_pmids (None when absent)."""
    def lookup(curie: str) -> np.ndarray | None:
        row = curie_to_pmids_db.execute(
            "SELECT pmids FROM curie_to_pmids WHERE curie = ?", (curie,)).fetchone()
        return None if row is None else np.frombuffer(row[0], dtype="<i4")
    return lookup
