#!/usr/bin/env python3
"""Build the curie_ngd SQLite database from curie_to_pmids and the Tier0 graph.

For every CURIE that has PMIDs and appears in the graph, store the NGD
(normalized Google distance) to each of its graph neighbors, sorted from
closest to farthest (ties by CURIE), with the smallest shared PMIDs (up to
--max-shared-pmids, ascending) that ARAX reports as the NGD edge's supporting
publications, plus the CURIE's own PMID count. This is the v1.1 format:

    curie_ngd(curie TEXT PRIMARY KEY, ngd TEXT, pmid_length INTEGER)
    ngd = [["NEIGHBOR:CURIE", 0.12, [1280572, 1293713, ...]], ...]

This reproduces Pathfinder's builder (Translator-CATRAX/pathfinder,
build_model/db_build/curie_ngd_builder.py): same neighbor definition (every
edge, both directions), same NGD formula and normalizer, same filtering, same
table. The difference is where the PMID sets live while intersecting: here
they are the sorted int32 arrays from curie_to_pmids, held in this process's
memory and shared with the worker processes by fork, instead of Redis sets.
"""

import argparse
import json
import logging
import math
import multiprocessing
import os
import sqlite3
import time

import numpy as np

# Filled in before the worker pool forks; workers read them without copying.
CURIES: list[str] = []
PMIDS: list[np.ndarray] = []
NEIGHBORS: dict[int, set[int]] = {}
LOG_NGD_NORMALIZER = 0.0
MAX_SHARED_PMIDS = 30


def calculate_ngd(log_of_length_of_first_pmid, length_of_second_pmid,
                  length_of_intersection, log_of_NGD_normalizer):
    """NGD of two PMID sets, exactly as Pathfinder's NGDCalculator.calculate_ngd."""
    if log_of_length_of_first_pmid == 0 or length_of_second_pmid == 0 or length_of_intersection == 0:
        return None

    log_of_length_of_second_element = math.log(length_of_second_pmid)
    try:
        return (max([log_of_length_of_first_pmid, log_of_length_of_second_element]) - math.log(
            length_of_intersection)) / \
            (log_of_NGD_normalizer - min([log_of_length_of_first_pmid, log_of_length_of_second_element]))
    except ValueError:
        return None


def intersect(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Values in both sorted, duplicate-free arrays, ascending: look up each
    value of the smaller array in the larger one."""
    if len(a) > len(b):
        a, b = b, a
    positions = np.searchsorted(b, a)
    positions[positions == len(b)] = 0
    return a[b[positions] == a]


def ngd_row(index: int) -> tuple[str, str, int]:
    """(curie, ngd JSON, pmid_length) for one CURIE."""
    pmids = PMIDS[index]
    ngds = []
    # Every neighbor here has at least one PMID (CURIEs without PMIDs were
    # never indexed), matching Pathfinder's nonzero-length filter.
    log_length = math.log(len(pmids))
    for neighbor in NEIGHBORS.get(index, ()):
        neighbor_pmids = PMIDS[neighbor]
        shared = intersect(pmids, neighbor_pmids)
        ngd = calculate_ngd(log_length, len(neighbor_pmids), len(shared), LOG_NGD_NORMALIZER)
        if ngd:  # Pathfinder drops both None and 0.0
            ngds.append((CURIES[neighbor], ngd, shared[:MAX_SHARED_PMIDS].tolist()))
    ngds.sort(key=lambda entry: (entry[1], entry[0]))
    return CURIES[index], json.dumps(ngds), len(pmids)


def load_pmids(curie_to_pmids_path: str) -> None:
    """Load every CURIE's PMID array into CURIES/PMIDS, in curie order."""
    conn = sqlite3.connect(f"file:{curie_to_pmids_path}?mode=ro", uri=True)
    for curie, blob in conn.execute("SELECT curie, pmids FROM curie_to_pmids ORDER BY curie"):
        pmids = np.frombuffer(blob, dtype=np.int32)
        if len(pmids):
            CURIES.append(curie)
            PMIDS.append(pmids)
    conn.close()


def load_neighbors(nodes_path: str, edges_path: str, index_of: dict[str, int]) -> list[bool]:
    """Fill NEIGHBORS from the graph, keeping only CURIEs that have PMIDs.
    Return which CURIEs appear in the graph at all: Pathfinder writes a row
    (possibly with no NGDs) for each of those and skips the rest."""
    in_graph = [False] * len(CURIES)
    with open(nodes_path, encoding="utf-8") as f:
        for line in f:
            index = index_of.get(json.loads(line).get("id"))
            if index is not None:
                in_graph[index] = True

    start = time.perf_counter()
    with open(edges_path, encoding="utf-8") as f:
        for n, line in enumerate(f, start=1):
            edge = json.loads(line)
            subject, obj = edge.get("subject"), edge.get("object")
            if not (subject and obj):
                continue
            subject_index, object_index = index_of.get(subject), index_of.get(obj)
            if subject_index is not None:
                in_graph[subject_index] = True
            if object_index is not None:
                in_graph[object_index] = True
            if subject_index is not None and object_index is not None:
                NEIGHBORS.setdefault(subject_index, set()).add(object_index)
                NEIGHBORS.setdefault(object_index, set()).add(subject_index)
            if n % 5_000_000 == 0:
                logging.info("  read %s edges (%.0f edges/sec)", f"{n:,}",
                             n / (time.perf_counter() - start))
    return in_graph


def main() -> None:
    """CLI entry point: load inputs, compute NGDs in parallel, write the DB."""
    global LOG_NGD_NORMALIZER, MAX_SHARED_PMIDS
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--curie-to-pmids", required=True,
                        help="curie_to_pmids SQLite database (int32 PMID blobs).")
    parser.add_argument("--nodes", required=True, help="Tier0 nodes.jsonl")
    parser.add_argument("--edges", required=True, help="Tier0 edges.jsonl")
    parser.add_argument("--output", required=True, help="curie_ngd SQLite database to write.")
    parser.add_argument("--num-pubmed-articles", type=float, default=3.5e7,
                        help="Number of PubMed citations used for NGD normalization "
                             "(default: 3.5e7, as in Pathfinder).")
    parser.add_argument("--avg-mesh-terms-per-article", type=int, default=20,
                        help="Average number of MeSH terms per article (default: 20, as in Pathfinder).")
    parser.add_argument("--max-shared-pmids", type=int, default=MAX_SHARED_PMIDS,
                        help="Shared PMIDs stored per neighbor, smallest first "
                             f"(default: {MAX_SHARED_PMIDS}, the number ARAX reports per NGD edge).")
    parser.add_argument("--workers", type=int, default=os.cpu_count())
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    LOG_NGD_NORMALIZER = math.log(args.num_pubmed_articles * args.avg_mesh_terms_per_article)
    MAX_SHARED_PMIDS = args.max_shared_pmids
    start = time.time()

    logging.info("Loading PMIDs from %s", args.curie_to_pmids)
    load_pmids(args.curie_to_pmids)
    index_of = {curie: i for i, curie in enumerate(CURIES)}
    logging.info("Loaded %s CURIEs with PMIDs", f"{len(CURIES):,}")

    logging.info("Reading graph neighbors from %s and %s", args.nodes, args.edges)
    in_graph = load_neighbors(args.nodes, args.edges, index_of)
    del index_of
    to_compute = [i for i, present in enumerate(in_graph) if present]
    logging.info("%s CURIEs are in the graph (%s skipped); %s neighbor pairs",
                 f"{len(to_compute):,}", f"{len(CURIES) - len(to_compute):,}",
                 f"{sum(len(s) for s in NEIGHBORS.values()):,}")

    if os.path.exists(args.output):
        os.remove(args.output)
    out = sqlite3.connect(args.output)
    out.execute("PRAGMA journal_mode=OFF")
    out.execute("PRAGMA synchronous=OFF")
    out.execute("""
        CREATE TABLE curie_ngd (
            curie TEXT NOT NULL PRIMARY KEY,
            ngd TEXT NOT NULL,
            pmid_length INTEGER NOT NULL DEFAULT 0
        )
    """)

    logging.info("Computing NGDs with %d workers", args.workers)
    written = 0
    # fork shares the loaded arrays with the workers instead of copying them.
    with multiprocessing.get_context("fork").Pool(args.workers) as pool:
        for row in pool.imap(ngd_row, to_compute, chunksize=256):
            out.execute("INSERT INTO curie_ngd VALUES (?, ?, ?)", row)
            written += 1
            if written % 100_000 == 0:
                out.commit()
                logging.info("  wrote %s / %s", f"{written:,}", f"{len(to_compute):,}")
    out.commit()
    out.close()
    logging.info("Done: %s CURIEs in %.1f min", f"{written:,}", (time.time() - start) / 60)


if __name__ == "__main__":
    main()
