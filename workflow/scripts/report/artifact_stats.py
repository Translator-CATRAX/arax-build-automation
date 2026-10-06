#!/usr/bin/env python3
"""Count what is in each release artifact.

The counts are saved with every build (artifact_stats.json) so that the next
build's report can show how each one changed. Artifacts are identified by kind
rather than filename, since filenames carry the graph version.

Run standalone to write stats for a build that predates the report, e.g.

    artifact_stats.py --output 20260621/artifact_stats.json \\
        --artifact curie_ngd=curie_ngd_v1.1_tier0-20260621.sqlite ...
"""

import argparse
import json
import pickle
import sqlite3
from pathlib import Path

KINDS = ("autocomplete", "tier0_info_for_overlay", "curie_to_pmids", "curie_ngd",
         "fda_approved_drugs")


def _connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _table_rows(conn: sqlite3.Connection, *tables: str) -> dict[str, int]:
    return {f"{table} rows": conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
            for table in tables}


def autocomplete(path: Path) -> dict[str, int]:
    """Names and IDs offered for autocompletion."""
    with _connect(path) as conn:
        return _table_rows(conn, "terms")


def tier0_info_for_overlay(path: Path) -> dict[str, int]:
    """Edges with publications, nodes with neighbor counts, and categories."""
    with _connect(path) as conn:
        return _table_rows(conn, "edge_publications", "neighbors", "category_counts")


def curie_to_pmids(path: Path) -> dict[str, int]:
    """CURIEs and (CURIE, PMID) pairs; each PMID is a 4-byte int in the blob."""
    with _connect(path) as conn:
        curies, pairs = conn.execute(
            "SELECT count(*), coalesce(sum(length(pmids)), 0) / 4 FROM curie_to_pmids").fetchone()
    return {"CURIEs": curies, "(CURIE, PMID) pairs": pairs}


def curie_ngd(path: Path) -> dict[str, int]:
    """CURIEs, how many have at least one neighbor NGD, and neighbor pairs."""
    with _connect(path) as conn:
        curies, with_neighbors, pairs = conn.execute(
            "SELECT count(*), sum(ngd != '[]'), coalesce(sum(json_array_length(ngd)), 0)"
            " FROM curie_ngd").fetchone()
    return {"CURIEs": curies, "CURIEs with neighbor NGDs": with_neighbors,
            "neighbor NGD pairs": pairs}


def fda_approved_drugs(path: Path) -> dict[str, int]:
    """Canonical CURIEs of FDA-approved drugs."""
    with open(path, "rb") as f:
        return {"drug CURIEs": len(pickle.load(f))}


def artifact_stats(artifacts: dict[str, Path]) -> dict[str, dict[str, int]]:
    """Counts for each artifact, keyed by kind."""
    stats = {}
    for kind, path in artifacts.items():
        if kind not in KINDS:
            raise ValueError(f"Unknown artifact kind {kind!r}; expected one of {', '.join(KINDS)}")
        stats[kind] = globals()[kind](Path(path))
    return stats


def main() -> None:
    """CLI entry point: count each --artifact and write the counts as JSON."""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--artifact", action="append", required=True, metavar="KIND=PATH",
                        help=f"An artifact to count; KIND is one of {', '.join(KINDS)}.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    stats = artifact_stats(dict(a.split("=", 1) for a in args.artifact))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(stats, indent=2) + "\n")


if __name__ == "__main__":
    main()
