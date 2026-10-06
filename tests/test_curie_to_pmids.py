"""curie_to_pmids, as ARAX's compute_ngd (Overlay/compute_ngd.py) and xCRG
(xcrg/pmid.py) read it."""

import itertools
import json

import numpy as np

from conftest import sample_rows

# Heavily studied concepts that must keep large PMID sets.
HUBS = {"MONDO:0005148": 100_000,  # type 2 diabetes mellitus
        "NCBIGene:7157": 10_000,   # TP53
        "CHEBI:15365": 10_000}     # acetylsalicylic acid


def test_schema(curie_to_pmids_db):
    # compute_ngd runs SELECT * and reads row[0], row[1], so column order matters.
    columns = curie_to_pmids_db.execute("PRAGMA table_info(curie_to_pmids)").fetchall()
    assert [(c[1], c[2], c[5]) for c in columns] == [("curie", "TEXT", 1), ("pmids", "BLOB", 0)]


def test_pmids_are_sorted_unique_int32_blobs(curie_to_pmids_db):
    for curie, blob in sample_rows(curie_to_pmids_db, "curie_to_pmids", "curie, pmids"):
        assert isinstance(blob, bytes) and blob and len(blob) % 4 == 0, curie
        pmids = np.frombuffer(blob, dtype=np.int32)
        assert pmids[0] > 0 and np.all(np.diff(pmids) > 0), curie


def test_pmids_are_in_range(curie_to_pmids_db):
    # PubMed IDs are in the tens of millions; anything far beyond means a blob
    # was written with the wrong width or byte order.
    for curie, blob in sample_rows(curie_to_pmids_db, "curie_to_pmids", "curie, pmids"):
        assert np.frombuffer(blob, dtype=np.int32)[-1] < 100_000_000, curie


def test_xcrg_read_path(curie_to_pmids_db):
    # xCRG decodes the blob as little-endian unsigned 32-bit ints; that must
    # give the same PMIDs ARAX gets from native int32.
    for curie, blob in sample_rows(curie_to_pmids_db, "curie_to_pmids", "curie, pmids", n=500):
        assert np.array_equal(np.frombuffer(blob, dtype="<u4"),
                              np.frombuffer(blob, dtype=np.int32)), curie


def test_tier0_edge_pmids_are_included(curie_to_pmids_db, graph_dir):
    # The build attaches each edge's PMID publications to both endpoints; if
    # that stage is skipped, the build only logs a warning.
    wanted: dict[str, set[int]] = {}
    with open(graph_dir / "edges.jsonl", encoding="utf-8") as f:
        for line in itertools.islice(f, 100_000):
            edge = json.loads(line)
            pmids = {int(p[5:]) for p in edge.get("publications") or ()
                     if p.startswith("PMID:") and p[5:].isdigit()}
            if pmids:
                for curie in (edge["subject"], edge["object"]):
                    wanted.setdefault(curie, set()).update(pmids)
    assert wanted, "no edges with PMIDs among the first 100,000"
    for curie, pmids in wanted.items():
        row = curie_to_pmids_db.execute("SELECT pmids FROM curie_to_pmids WHERE curie = ?",
                                        (curie,)).fetchone()
        assert row, f"{curie} has edge PMIDs but no row"
        stored = np.frombuffer(row[0], dtype=np.int32)
        expected = np.array(sorted(pmids), dtype=np.int32)
        found = stored[np.minimum(np.searchsorted(stored, expected), len(stored) - 1)] == expected
        assert found.all(), (curie, expected[~found][:5].tolist())


def test_name_collision_override(curie_to_pmids_db):
    # Babel maps the MeSH heading "Male" to the E. coli malE gene; the build
    # overrides it so its PMIDs go to the MeSH sex descriptor instead.
    rows = dict(curie_to_pmids_db.execute(
        "SELECT curie, length(pmids) / 4 FROM curie_to_pmids"
        " WHERE curie IN ('MESH:D008297', 'NCBIGene:69652262')").fetchall())
    assert rows.get("MESH:D008297", 0) > 1_000_000
    assert rows.get("NCBIGene:69652262", 0) < 1_000


def test_curies_are_babel_preferred(curie_to_pmids_db, is_babel_preferred):
    # PMIDs must be stored under the ID that runtime lookups use: names resolved
    # from PubMed are walked to their clique's preferred CURIE, and graph IDs
    # are already preferred. A Babel release that does not match the graph
    # breaks this.
    curies = [row[0] for row in sample_rows(curie_to_pmids_db, "curie_to_pmids", "curie")]
    not_preferred = [c for c in curies if not is_babel_preferred(c)]
    assert not not_preferred, not_preferred[:5]


def test_arax_lookup_of_hubs(curie_to_pmids_db):
    # The query compute_ngd uses to load PMIDs for a batch of CURIEs.
    rows = curie_to_pmids_db.execute(
        f"SELECT * FROM curie_to_pmids WHERE curie in ({','.join(repr(c) for c in HUBS)})"
    ).fetchall()
    found = {curie: len(np.frombuffer(blob, dtype=np.int32)) for curie, blob in rows}
    for curie, minimum in HUBS.items():
        assert found.get(curie, 0) >= minimum, f"{curie}: {found.get(curie)} PMIDs"
