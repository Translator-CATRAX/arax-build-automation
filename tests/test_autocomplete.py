"""The autocomplete database, as RTX code/autocomplete/rtxcomplete.py queries it."""

import pytest


def get_nodes_like(conn, word: str, limit: int = 20) -> list[str]:
    """rtxcomplete.get_nodes_like's first query, verbatim, with its de-duplication."""
    floor = word[:-1]
    ceiling = floor + 'zz'
    rows = conn.execute(f"SELECT term FROM terms WHERE term > \"{floor}\" AND term < \"{ceiling}\""
                        f" AND term LIKE \"{word}%%\" ORDER BY length(term),term LIMIT {limit}")
    seen, terms = set(), []
    for (term,) in rows:
        if term.upper() not in seen:
            seen.add(term.upper())
            terms.append(term)
    return terms


def test_schema(autocomplete_db):
    sql = dict(autocomplete_db.execute("SELECT name, sql FROM sqlite_master"))
    # Case-insensitive matching and the index the range query relies on.
    assert "COLLATE NOCASE" in sql["terms"]
    assert "idx_terms_term" in sql


@pytest.mark.parametrize("typed, expected", [
    ("acetamin", "acetaminophen"),
    ("ACETAMIN", "acetaminophen"),
    ("metform", "metformin"),
    ("type 2 diabetes mel", "type 2 diabetes mellitus"),
    ("MONDO:000514", "MONDO:0005148"),
    ("TP5", "TP53"),
])
def test_prefix_completion(autocomplete_db, typed, expected):
    assert expected.upper() in {t.upper() for t in get_nodes_like(autocomplete_db, typed, 1000)}


@pytest.mark.parametrize("typed, expected", [
    ("cetaminophe", "acetaminophen"),
    ("diabetes mellitus", "type 2 diabetes mellitus"),
])
def test_substring_completion(autocomplete_db, typed, expected):
    # rtxcomplete.get_nodes_like's fallback query, verbatim.
    rows = autocomplete_db.execute("SELECT term FROM terms WHERE term LIKE \"%%%s%%\""
                                   " ORDER BY length(term),term LIMIT %s" % (typed, 10000))
    assert expected.upper() in {term.upper() for (term,) in rows}


def test_every_graph_node_is_searchable(autocomplete_db, graph_node_ids):
    terms = {term.upper() for (term,) in autocomplete_db.execute("SELECT term FROM terms")}
    missing = sorted(i for i in graph_node_ids if i.upper() not in terms)
    assert not missing, missing[:5]


def test_no_empty_or_case_duplicate_terms(autocomplete_db):
    assert autocomplete_db.execute("SELECT count(*) FROM terms WHERE trim(term) = ''").fetchone()[0] == 0
    duplicates = autocomplete_db.execute(
        "SELECT upper(term) FROM terms GROUP BY upper(term) HAVING count(*) > 1 LIMIT 5").fetchall()
    assert not duplicates
