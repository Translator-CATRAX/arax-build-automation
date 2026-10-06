"""fda_approved_drugs, which ARAX_expander loads with pickle and uses to keep or
drop answer nodes for the highest_FDA_approval_status constraint, so its
entries must be canonical CURIEs like the node IDs ARAX returns."""

import re


def test_is_a_set_of_curies(fda_approved_drugs):
    assert isinstance(fda_approved_drugs, set)
    bad = [c for c in fda_approved_drugs
           if not (isinstance(c, str) and re.fullmatch(r"[A-Za-z][\w.]*:\S+", c))]
    assert not bad, bad[:5]


def test_ids_are_babel_preferred(fda_approved_drugs, is_babel_preferred):
    # ARAX matches these against node IDs, which are Babel-preferred CURIEs.
    not_preferred = sorted(c for c in fda_approved_drugs if not is_babel_preferred(c))
    assert not not_preferred, not_preferred[:5]


def test_contains_known_approved_drugs(fda_approved_drugs):
    # Canonical (CHEBI) IDs of acetaminophen, metformin, imatinib and aspirin.
    assert {"CHEBI:46195", "CHEBI:6801", "CHEBI:45783", "CHEBI:15365"} <= fda_approved_drugs

