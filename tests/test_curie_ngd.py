"""curie_ngd in the v1.1 format: ngd is a JSON list of
[neighbor CURIE, NGD, [up to 30 smallest shared PMIDs]], closest first. Read by
ARAX's compute_ngd (ComputeNGD._lookup_precomputed_ngd), Pathfinder and xCRG."""

import ast
import json
import math

import numpy as np
import pytest

from conftest import sample_rows

NGD_NORMALIZER = 3.5e+7 * 20
MAX_SHARED_PMIDS = 30


def _reference_ngd(pmid_map, subject_curie, object_curie):
    """Ported verbatim from RTX code/ARAX/test/test_compute_ngd.py, where it is the
    reference calculation ARAX's own NGD code must match."""
    if subject_curie not in pmid_map or object_curie not in pmid_map:
        return math.nan, set()
    pubmed_id_sets = [set(pmid_map[subject_curie]), set(pmid_map[object_curie])]
    joint_pubmed_ids = pubmed_id_sets[0].intersection(pubmed_id_sets[1])
    marginal_counts = [len(s) for s in pubmed_id_sets]
    joint_count = len(joint_pubmed_ids)
    if 0 in marginal_counts or joint_count == 0:
        return math.nan, joint_pubmed_ids
    value = ((max(math.log(count) for count in marginal_counts) - math.log(joint_count)) /
             (math.log(NGD_NORMALIZER) - min(math.log(count) for count in marginal_counts)))
    return value, joint_pubmed_ids


@pytest.fixture(scope="module")
def sample(curie_ngd_db):
    """Sampled rows that have at least one neighbor, with ngd parsed."""
    rows = [(curie, json.loads(ngd), ngd, pmid_length)
            for curie, ngd, pmid_length in sample_rows(curie_ngd_db, "curie_ngd",
                                                       "curie, ngd, pmid_length", n=5000)]
    rows = [row for row in rows if row[1]]
    assert len(rows) >= 100, "too few sampled CURIEs have neighbor NGDs"
    return rows


def test_schema(curie_ngd_db):
    columns = curie_ngd_db.execute("PRAGMA table_info(curie_ngd)").fetchall()
    assert [(c[1], c[2], c[3], c[5]) for c in columns] == [
        ("curie", "TEXT", 1, 1), ("ngd", "TEXT", 1, 0), ("pmid_length", "INTEGER", 1, 0)]


def test_entry_format(sample):
    for curie, entries, text, _ in sample:
        assert json.dumps(entries) == text, f"{curie}: not serialized like v1.1"
        for neighbor, ngd, shared in entries:
            assert isinstance(neighbor, str) and neighbor != curie, curie
            assert isinstance(ngd, float) and math.isfinite(ngd) and ngd > 0, (curie, neighbor)
            assert 1 <= len(shared) <= MAX_SHARED_PMIDS, (curie, neighbor)
            assert all(isinstance(p, int) for p in shared) and shared == sorted(set(shared))
        ngds = [entry[1] for entry in entries]
        assert ngds == sorted(ngds), f"{curie}: neighbors not sorted closest first"


def test_pmid_length_matches_curie_to_pmids(sample, pmids_of):
    for curie, _, _, pmid_length in sample[:500]:
        assert pmid_length == len(pmids_of(curie)), curie


def test_ngd_and_shared_pmids_match_curie_to_pmids(sample, pmids_of):
    for curie, entries, _, _ in sample[:200]:
        for neighbor, ngd, shared in entries[:10]:
            pmid_map = {curie: pmids_of(curie), neighbor: pmids_of(neighbor)}
            expected_ngd, joint = _reference_ngd(pmid_map, curie, neighbor)
            assert ngd == pytest.approx(expected_ngd, rel=1e-9), (curie, neighbor)
            assert shared == sorted(joint)[:MAX_SHARED_PMIDS], (curie, neighbor)


def test_every_curie_is_in_curie_to_pmids(curie_ngd_db, curie_to_pmids_db):
    # Both are built from the same curie_to_pmids; a CURIE here without PMIDs
    # there means the two files came from different builds.
    path = curie_to_pmids_db.execute("PRAGMA database_list").fetchone()[2]
    curie_ngd_db.execute("ATTACH DATABASE ? AS p", (f"file:{path}?mode=ro",))
    try:
        orphans = curie_ngd_db.execute(
            "SELECT curie FROM curie_ngd AS n WHERE NOT EXISTS"
            " (SELECT 1 FROM p.curie_to_pmids AS c WHERE c.curie = n.curie) LIMIT 5").fetchall()
    finally:
        curie_ngd_db.execute("DETACH DATABASE p")
    assert not orphans


def test_no_missing_rows(curie_ngd_db, curie_to_pmids_db, graph_node_ids):
    # Every CURIE that has PMIDs and is in the graph gets a row, even if it has
    # no neighbor NGDs; the opposite direction is test_every_curie_is_in_curie_to_pmids.
    rows = {curie for (curie,) in curie_ngd_db.execute("SELECT curie FROM curie_ngd")}
    missing = [curie for (curie,) in curie_to_pmids_db.execute("SELECT curie FROM curie_to_pmids")
               if curie in graph_node_ids and curie not in rows]
    assert not missing, missing[:5]


def test_pathfinder_read_path(sample, curie_ngd_db):
    # Pathfinder (NGDRepository.get_curie_ngd, feature_extractor.get_neighbors_info)
    # parses ngd with ast.literal_eval and reads each entry by index.
    for curie, entries, text, _ in sample[:500]:
        parsed = ast.literal_eval(text)
        assert [(e[0], e[1]) for e in parsed] == [(e[0], e[1]) for e in entries], curie
    # NGDRepository.get_curies_pmid_length, which batches 900 CURIEs per query.
    curies = [row[0] for row in sample[:900]]
    rows = curie_ngd_db.execute(
        f"SELECT curie, pmid_length FROM curie_ngd WHERE curie IN ({','.join('?' for _ in curies)})",
        curies).fetchall()
    assert len(rows) == len(curies) and all(length > 0 for _, length in rows)


def test_xcrg_read_path(sample):
    # xcrg/ngd.py: unpacks exactly three values per entry and keeps finite,
    # positive scores; anything else is caught and leaves xCRG with no NGDs.
    for curie, _, text, _ in sample[:500]:
        kept = {}
        for neighbor, score, _ in json.loads(text):
            if math.isfinite(float(score)) and float(score) > 0:
                kept[str(neighbor)] = float(score)
        assert kept, curie


def test_arax_lookup_of_known_pair(curie_ngd_db):
    # Type 2 diabetes and its best-known drug.
    row = curie_ngd_db.execute("SELECT ngd FROM curie_ngd WHERE curie = ?",
                               ("MONDO:0005148",)).fetchone()
    neighbors = {entry[0]: entry for entry in json.loads(row[0])}
    assert "CHEBI:6801" in neighbors  # metformin
    _, ngd, shared = neighbors["CHEBI:6801"]
    assert 0 < ngd < 1 and len(shared) == MAX_SHARED_PMIDS
