#!/usr/bin/env python3

"""
Build the tier0-info-for-overlay SQLite database from conflated tier0 JSONL files.

This database is intentionally minimal. It is *not* a full ingest of the
Tier0 graph. It only contains what the ARAX Overlay modules need:

    * edge_publications  - per-edge PMID lists, keyed by canonical ARAX edge
                           key. Used by Overlay/NGD-style decoration paths.
    * neighbors          - per-node neighbor counts by expanded biolink
                           category. Used by Overlay/fisher_exact_test.py.
    * category_counts    - node count per expanded biolink category. Also
                           used by Overlay/fisher_exact_test.py.

There are no `nodes` or `edges` tables. Anything that needs full node/edge
properties should query Tier0/Gandalf directly.

The canonical ARAX edge key produced here MUST match
`ARAXQuery/util.get_arax_edge_key(edge)`, otherwise downstream lookups
will silently miss.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from biolink_helper_pkg import BiolinkHelper


TIER0_ARRAY_DELIMITER = "ǂ"


# ---------------------------------------------------------------------
# Canonical ARAX edge key (mirror of util.get_arax_edge_key)
# ---------------------------------------------------------------------

def _extract_primary_knowledge_source(sources: Any) -> str:
    if not sources or not isinstance(sources, list):
        return ""
    for source in sources:
        if isinstance(source, dict) and source.get("resource_role") == "primary_knowledge_source":
            return source.get("resource_id") or ""
    return ""


def _get_arax_edge_key(
    subject: str,
    predicate: str,
    obj: str,
    primary_knowledge_source: str,
    qualified_predicate: Optional[str],
    object_direction_qualifier: Optional[str],
    object_aspect_qualifier: Optional[str],
) -> str:
    qualified_portion = "--".join([
        qualified_predicate or "",
        object_direction_qualifier or "",
        object_aspect_qualifier or "",
    ])
    return "--".join([
        subject,
        predicate,
        qualified_portion,
        obj,
        primary_knowledge_source,
    ])


def _encode_publications(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, Iterable):
        cleaned = [str(item) for item in value if item]
        return TIER0_ARRAY_DELIMITER.join(cleaned)
    return str(value)


# ---------------------------------------------------------------------
# Streaming edge pass: drives both edge_publications inserts and
# neighbor accumulation.
# ---------------------------------------------------------------------

def _iter_edge_publication_rows(
    edges_path: Path,
    node_index: Dict[str, int],
    labeled: List[bool],
    neighbors: Dict[int, Set[int]],
    progress_every: int = 1_000_000,
) -> Iterable[tuple]:
    """
    Yield `(arax_edge_key, publications_str)` rows for edges that have at
    least one publication. As a side effect, populate `neighbors` with
    `node -> set(neighbors that have at least one category)`, both as
    integer node indexes, so the caller can count neighbors per category
    after this pass finishes. Storing each neighbor once (not once per
    category) is what keeps this pass's memory down.

    Edge endpoints missing from the nodes file get a new index on the fly;
    they have no categories, so they are never counted as anyone's neighbor.
    """
    def index_of(node_id: str) -> int:
        index = node_index.get(node_id)
        if index is None:
            index = node_index[node_id] = len(labeled)
            labeled.append(False)
        return index

    with edges_path.open() as f:
        for index, line in enumerate(f, start=1):
            edge = json.loads(line)

            subject_id = edge["subject"]
            object_id = edge["object"]
            subject_index = index_of(subject_id)
            object_index = index_of(object_id)

            if labeled[object_index]:
                neighbors[subject_index].add(object_index)
            if labeled[subject_index]:
                neighbors[object_index].add(subject_index)

            publications = _encode_publications(edge.get("publications"))
            if not publications:
                if index % progress_every == 0:
                    logging.info("  ... streamed %s edges", f"{index:,}")
                continue

            arax_edge_key = _get_arax_edge_key(
                subject=subject_id,
                predicate=edge["predicate"],
                obj=object_id,
                primary_knowledge_source=_extract_primary_knowledge_source(edge.get("sources")),
                qualified_predicate=edge.get("qualified_predicate"),
                object_direction_qualifier=edge.get("object_direction_qualifier"),
                object_aspect_qualifier=edge.get("object_aspect_qualifier"),
            )

            yield (arax_edge_key, publications)

            if index % progress_every == 0:
                logging.info("  ... streamed %s edges", f"{index:,}")


# ---------------------------------------------------------------------
# Node loader (categories only — the rest of each node is not needed)
# ---------------------------------------------------------------------

def _load_node_categories(path: Path) -> Dict[str, Tuple[str, ...]]:
    result: Dict[str, Tuple[str, ...]] = {}
    with path.open() as f:
        for line in f:
            node = json.loads(line)
            category = node.get("category") or ()
            result[node["id"]] = tuple(category) if isinstance(category, list) else (category,)
    return result


# ---------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------

def create_tier0_overlay_sqlite_db(
    nodes_path: Path,
    edges_path: Path,
    output_db: Path,
    biolink_version: str,
    biolink_cache_dir: Path,
) -> None:
    """Build the tier0-info-for-overlay SQLite database (edge_publications,
    neighbors, category_counts) from Tier0 nodes and edges JSONL files."""
    if output_db.exists():
        output_db.unlink()

    connection = sqlite3.connect(output_db)

    # -------------------------
    # Expanded biolink labels per node — drives `neighbors` and
    # `category_counts`. Tier0 nodes carry only a singular `category`;
    # we expand to ancestors + mixins so Fisher Exact Test queries by a
    # parent category like biolink:ChemicalEntity still resolve.
    # -------------------------

    logging.info("Loading node categories...")
    categories_by_node = _load_node_categories(nodes_path)
    n_nodes = len(categories_by_node)
    logging.info("Loaded %s nodes", f"{n_nodes:,}")

    # Nodes are numbered in nodes-file order. Each node points at a "label
    # set": the sorted tuple of its expanded categories. Nodes with the same
    # categories share one label set, so the expansion runs once per distinct
    # category combination, not once per node.
    logging.info("Expanding biolink category ancestors for each node...")
    biolink_cache_dir.mkdir(parents=True, exist_ok=True)
    bh = BiolinkHelper(biolink_version, str(biolink_cache_dir))
    node_ids: List[str] = list(categories_by_node)
    node_index: Dict[str, int] = {node_id: i for i, node_id in enumerate(node_ids)}
    label_set_ids: Dict[Tuple[str, ...], int] = {}
    label_sets: List[Tuple[str, ...]] = []
    expansion_by_categories: Dict[Tuple[str, ...], int] = {}
    node_label_set: List[int] = []
    for categories in categories_by_node.values():
        set_id = expansion_by_categories.get(categories)
        if set_id is None:
            labels = tuple(sorted(bh.get_ancestors(list(categories), include_mixins=True))) if categories else ()
            set_id = label_set_ids.setdefault(labels, len(label_sets))
            if set_id == len(label_sets):
                label_sets.append(labels)
            expansion_by_categories[categories] = set_id
        node_label_set.append(set_id)
    del categories_by_node, expansion_by_categories
    labeled: List[bool] = [bool(label_sets[set_id]) for set_id in node_label_set]
    logging.info("%s distinct category sets", f"{len(label_sets):,}")

    # -------------------------
    # edge_publications: streamed from the edges JSONL, only rows with
    # at least one publication. Same pass populates the neighbor
    # accumulator (consumed below).
    # -------------------------

    connection.execute(
        "CREATE TABLE edge_publications ("
        "arax_edge_key TEXT PRIMARY KEY, "
        "publications  TEXT"
        ")"
    )

    neighbors: Dict[int, Set[int]] = defaultdict(set)

    logging.info("Streaming edges and writing edge_publications rows...")
    changes_before = connection.total_changes
    connection.executemany(
        "INSERT OR IGNORE INTO edge_publications (arax_edge_key, publications) VALUES (?, ?)",
        _iter_edge_publication_rows(edges_path, node_index, labeled, neighbors),
    )
    inserted = connection.total_changes - changes_before
    logging.info("Inserted %s edge_publications rows", f"{inserted:,}")
    connection.commit()

    # -------------------------
    # neighbors
    # -------------------------

    # Edge endpoints that were not in the nodes file got indexes during the
    # edge pass; extend the id list to cover them.
    node_ids.extend([None] * (len(labeled) - len(node_ids)))
    for node_id, index in node_index.items():
        if index >= n_nodes:
            node_ids[index] = node_id
    del node_index

    def neighbor_rows():
        # A node's count for a category is the number of its distinct
        # neighbors that have that category: count neighbors per label set,
        # then add each label set's count to every category in it.
        for index, neighbor_indexes in neighbors.items():
            per_label_set = Counter(node_label_set[n] for n in neighbor_indexes)
            counts: Dict[str, int] = defaultdict(int)
            for set_id, count in per_label_set.items():
                for label in label_sets[set_id]:
                    counts[label] += count
            yield node_ids[index], json.dumps(dict(sorted(counts.items())))

    logging.info("Writing neighbors table (%s nodes)...", f"{len(neighbors):,}")
    connection.execute(
        "CREATE TABLE neighbors (id TEXT PRIMARY KEY, neighbor_counts TEXT)"
    )
    connection.executemany(
        "INSERT INTO neighbors (id, neighbor_counts) VALUES (?, ?)",
        neighbor_rows(),
    )
    connection.commit()
    del neighbors

    # -------------------------
    # category_counts
    # -------------------------

    logging.info("Writing category_counts table...")
    connection.execute(
        "CREATE TABLE category_counts (category TEXT PRIMARY KEY, count INTEGER)"
    )
    nodes_by_label: Dict[str, int] = defaultdict(int)
    for set_id, count in Counter(node_label_set[:n_nodes]).items():
        for label in label_sets[set_id]:
            nodes_by_label[label] += count
    connection.executemany(
        "INSERT INTO category_counts (category, count) VALUES (?, ?)",
        nodes_by_label.items(),
    )
    connection.commit()

    connection.close()


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------

def _read_biolink_version(graph_metadata: Path) -> str:
    """Biolink version the graph was built with. Newer graph-metadata.json
    files use the key `translator:biolinkVersion`; older ones `biolinkVersion`."""
    metadata = json.loads(graph_metadata.read_text())
    for key in ("translator:biolinkVersion", "biolinkVersion"):
        if metadata.get(key):
            return metadata[key]
    raise KeyError(f"No Biolink version in {graph_metadata}")


def _build_output_filename(output_dir: Path, tier0_build_date: str, schema_version: str) -> Path:
    return output_dir / f"tier0-info-for-overlay_v{schema_version}_tier0-{tier0_build_date}.sqlite"


def main() -> None:
    """CLI entry point: parse args and build the overlay sqlite database."""
    parser = argparse.ArgumentParser(
        description="Build the minimal tier0-info-for-overlay SQLite database "
                    "(edge_publications, neighbors, category_counts) from Tier0 JSONL files."
    )
    parser.add_argument("--nodes", type=Path, required=True,
                        help="Path to the conflated Tier0 nodes JSONL file.")
    parser.add_argument("--edges", type=Path, required=True,
                        help="Path to the conflated Tier0 edges JSONL file.")
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="Directory to write the resulting sqlite file into.")
    parser.add_argument("--tier0-build-date", required=True,
                        help="Tier0 build date stamp in YYYYMMDD form (used in the filename).")
    parser.add_argument("--schema-version", default="1.0",
                        help="Schema version stamp embedded in the filename. Default: 1.0")
    parser.add_argument(
        "--graph-metadata",
        type=Path,
        required=True,
        help="Tier0 graph-metadata.json. Category-ancestor expansion always "
             "uses the Biolink version the graph was built with, read from here.",
    )
    parser.add_argument(
        "--biolink-cache-dir",
        type=Path,
        required=True,
        help="Directory where the Biolink model YAML and lookup map are "
             "downloaded and cached.",
    )

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    biolink_version = _read_biolink_version(args.graph_metadata)
    logging.info("Biolink version (from %s): %s", args.graph_metadata, biolink_version)

    output_db = _build_output_filename(args.output_dir, args.tier0_build_date, args.schema_version)
    logging.info("Output: %s", output_db)

    create_tier0_overlay_sqlite_db(
        args.nodes, args.edges, output_db, biolink_version, args.biolink_cache_dir
    )

    logging.info("Done.")


if __name__ == "__main__":
    main()
