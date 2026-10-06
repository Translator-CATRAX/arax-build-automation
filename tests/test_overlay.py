"""tier0-info-for-overlay, which ARAX uses as both kg2c_sqlite and tier0_sqlite.
The neighbors and category_counts tables are read by the Fisher exact test
(Overlay/fisher_exact_test.py) and by Pathfinder's node degree lookup."""

import json

import pytest

from conftest import sample_rows

# Categories the Fisher exact test is commonly asked about.
FET_CATEGORIES = ["biolink:NamedThing", "biolink:Gene", "biolink:Protein",
                  "biolink:ChemicalEntity", "biolink:SmallMolecule", "biolink:Disease",
                  "biolink:PhenotypicFeature", "biolink:BiologicalProcess", "biolink:Pathway",
                  "biolink:AnatomicalEntity", "biolink:Cell"]


@pytest.fixture(scope="module")
def category_counts(overlay_db) -> dict[str, int]:
    return dict(overlay_db.execute("SELECT category, count FROM category_counts"))


def test_schema(overlay_db):
    tables = {name: sql for name, sql in overlay_db.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'table'")}
    assert set(tables) == {"edge_publications", "neighbors", "category_counts"}
    columns = {table: [(c[1], c[2], c[5]) for c in overlay_db.execute(f"PRAGMA table_info({table})")]
               for table in tables}
    assert columns == {
        "edge_publications": [("arax_edge_key", "TEXT", 1), ("publications", "TEXT", 0)],
        "neighbors": [("id", "TEXT", 1), ("neighbor_counts", "TEXT", 0)],
        "category_counts": [("category", "TEXT", 1), ("count", "INTEGER", 0)],
    }


def test_fet_category_count_query(overlay_db):
    # fisher_exact_test reads rows[0][0], so a missing category crashes it.
    for category in FET_CATEGORIES:
        rows = overlay_db.execute(
            f"SELECT C.count FROM category_counts AS C WHERE C.category = '{category}'").fetchall()
        assert rows and rows[0][0] > 0, category


def test_category_counts_follow_biolink_hierarchy(category_counts, biolink_helper):
    # Each node is counted under its categories' ancestors too, so every
    # ancestor must be present and no category can outnumber its ancestors.
    for category, count in category_counts.items():
        for ancestor in biolink_helper.get_ancestors([category], include_mixins=True):
            assert ancestor in category_counts, (category, ancestor)
            assert count <= category_counts[ancestor], (category, ancestor)


def test_neighbor_counts(overlay_db, category_counts):
    ids = [row[0] for row in sample_rows(overlay_db, "neighbors", "id")]
    # The query fisher_exact_test uses to load neighbor counts.
    rows = overlay_db.execute(
        f"SELECT N.id, N.neighbor_counts FROM neighbors AS N WHERE N.id IN "
        f"({','.join('?' * len(ids))})", ids).fetchall()
    assert len(rows) == len(ids)
    for node_id, text in rows:
        counts = json.loads(text)
        # Pathfinder takes a node's degree from its NamedThing count.
        assert counts.get("biolink:NamedThing", 0) > 0, node_id
        for category, count in counts.items():
            assert category in category_counts, (node_id, category)
            assert 0 < count <= category_counts[category], (node_id, category)


def test_neighbor_ids_are_graph_nodes(overlay_db, graph_node_ids):
    strays = [i for (i,) in overlay_db.execute("SELECT id FROM neighbors") if i not in graph_node_ids]
    assert not strays, strays[:5]


def test_edge_publications(overlay_db):
    for key, publications in sample_rows(overlay_db, "edge_publications", "*", n=20000):
        # util.get_arax_edge_key: subject--predicate--qualified predicate--object
        # direction--object aspect--object--primary knowledge source
        fields = key.split("--")
        assert len(fields) == 7 and fields[0] and fields[1].startswith("biolink:") \
            and fields[5] and fields[6].startswith("infores:"), key
        # ǂ-delimited publication IDs (PMID:, MGI:, ...), none empty or padded.
        for publication in publications.split("ǂ"):
            assert publication and publication == publication.strip() and ":" in publication, \
                (key, publications)
