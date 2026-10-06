#!/usr/bin/env python3
"""Write the build report: what was built, from what, how long it took, and
how each artifact's counts changed since the previous build.

The previous build is the newest YYYYMMDD directory under --records-dir (and
older than this build) that has an artifact_stats.json. This build's counts
are saved to --stats-output, and from there to its own build record, for the
next build to compare against.
"""

import argparse
import json
import re
import socket
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

from artifact_stats import artifact_stats

STATS_FILENAME = "artifact_stats.json"


def previous_build(records_dir: Path, version: str) -> Path | None:
    """The newest earlier build record that saved artifact stats."""
    if not records_dir.is_dir():
        return None
    earlier = [d for d in records_dir.iterdir()
               if re.fullmatch(r"\d{8}", d.name) and d.name < version
               and (d / STATS_FILENAME).exists()]
    return max(earlier, default=None)


def read_benchmark(path: Path) -> dict | None:
    """Runtime (s), peak memory (MB) and start and finish times of the rule
    that wrote a benchmark. Memory is PSS, which splits pages shared between
    processes; RSS would count the arrays curie_ngd's forked workers share
    once per worker."""
    if not path.exists():
        return None
    header, values = path.read_text().splitlines()[:2]
    row = dict(zip(header.split("\t"), values.split("\t")))
    finished = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    return {"seconds": float(row["s"]), "max_pss_mb": float(row["max_pss"]),
            "started": finished - timedelta(seconds=float(row["s"])), "finished": finished}


def read_tests(path: Path) -> dict[str, int | float]:
    """Totals from a pytest JUnit XML file."""
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "time": 0.0}
    for suite in ET.parse(path).getroot().iter("testsuite"):
        for key in totals:
            totals[key] += type(totals[key])(suite.get(key, 0))
    return totals


def failed_tests(path: Path) -> list[str]:
    """Names of the tests that failed or errored."""
    return [f"{case.get('classname')}::{case.get('name')}"
            for case in ET.parse(path).getroot().iter("testcase")
            if case.find("failure") is not None or case.find("error") is not None]


def duration(seconds: float) -> str:
    """Seconds as h:mm:ss."""
    minutes, secs = divmod(round(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


def size(n_bytes: int) -> str:
    """A byte count in the largest unit that keeps it at or above 1."""
    for unit in ("B", "KB", "MB", "GB"):
        if n_bytes < 1024 or unit == "GB":
            return f"{n_bytes:.0f} {unit}" if unit == "B" else f"{n_bytes:.1f} {unit}"
        n_bytes /= 1024
    raise AssertionError


def change(old: int | None, new: int) -> tuple[str, str]:
    """(absolute change, percent change) as table cells."""
    if old is None:
        return "new", ""
    pct = f"{(new - old) / old * 100:+.1f}%" if old else ""
    return f"{new - old:+,}", pct


def main() -> None:
    """CLI entry point: gather everything about the build and write the report."""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--records-dir", type=Path, required=True,
                        help="Build records, one YYYYMMDD directory per build.")
    parser.add_argument("--artifact", action="append", required=True, metavar="KIND=PATH",
                        help="A release artifact; KIND as in artifact_stats.py.")
    parser.add_argument("--config-dbs", type=Path, required=True)
    parser.add_argument("--test-results", type=Path, action="append", required=True,
                        help="pytest JUnit XML; repeat for each test step.")
    parser.add_argument("--graph-dir", type=Path, required=True)
    parser.add_argument("--pubmed-manifest", type=Path, required=True)
    parser.add_argument("--drugbank", type=Path, required=True)
    parser.add_argument("--babel", type=Path, required=True)
    parser.add_argument("--biolink-version", required=True)
    parser.add_argument("--stats-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    build_dir = args.build_dir
    artifacts = {kind: Path(path) for kind, path in (a.split("=", 1) for a in args.artifact)}

    stats = artifact_stats(artifacts)
    args.stats_output.write_text(json.dumps(stats, indent=2) + "\n")

    previous_dir = previous_build(args.records_dir, build_dir.name)
    previous = json.loads((previous_dir / STATS_FILENAME).read_text()) if previous_dir else {}

    # The build can span several Snakemake runs (a resumed build reruns only
    # what is missing), so time it from the first step's start to the last
    # step's finish rather than by any one run.
    benchmarks = [read_benchmark(p) for p in sorted((build_dir / "benchmarks").glob("*.tsv"))]
    started = min(b["started"] for b in benchmarks)
    built = max(b["finished"] for b in benchmarks)
    finished = datetime.now(timezone.utc)
    tests = {key: sum(read_tests(path)[key] for path in args.test_results)
             for key in ("tests", "failures", "errors", "skipped", "time")}
    pubmed = json.loads(args.pubmed_manifest.read_text())

    lines = [
        f"# ARAX database build {build_dir.name}",
        "",
        f"Written {finished.astimezone():%Y-%m-%d %H:%M %Z} on {socket.gethostname()}.",
        "",
        "## Inputs",
        "",
        f"- Tier0 graph: `{args.graph_dir}`",
        f"- PubMed: {pubmed['baseline_release']} baseline, {pubmed['baseline']['files']:,}"
        f" baseline + {pubmed['updatefiles']['files']:,} update files"
        f" (mirrored {pubmed['finished_at'][:10]})",
        f"- DrugBank: `{args.drugbank.name}`",
        f"- Babel: `{args.babel.name}`",
        f"- Biolink model: {args.biolink_version}",
        "",
        "## Run",
        "",
        f"- Build time: {duration((built - started).total_seconds())}, from the first step"
        f" starting ({started.astimezone():%Y-%m-%d %H:%M}) to the last one finishing"
        f" ({built.astimezone():%Y-%m-%d %H:%M})",
        f"- Tests: {tests['tests'] - tests['failures'] - tests['errors'] - tests['skipped']}"
        f" passed, {tests['failures'] + tests['errors']} failed, {tests['skipped']} skipped"
        f" in {tests['time']:.0f} s"
        f" ({', '.join(f'`{p.parent.name}/{p.name}`' for p in args.test_results)})",
    ]
    for name in (n for path in args.test_results for n in failed_tests(path)):
        lines.append(f"  - FAILED `{name}`")

    lines += [
        "",
        "## Artifacts",
        "",
        "Step time and peak memory (PSS) are for the rule that built the artifact.",
        "",
        "| Artifact | Size | MD5 | Step time | Peak memory | Built | Log |",
        "|---|---:|---|---:|---:|---|---|",
    ]
    for path in [*artifacts.values(), args.config_dbs]:
        md5_file = build_dir / "md5_sums" / f"{path.name}.md5"
        md5 = f"`{md5_file.read_text().split()[0]}`" if md5_file.exists() else "—"
        bench = read_benchmark(build_dir / "benchmarks" / f"{path.name}.tsv")
        log = build_dir / "logs" / f"{path.name}.log"
        lines.append(
            f"| `{path.name}` | {size(path.stat().st_size)} | {md5} "
            f"| {duration(bench['seconds']) if bench else ''} "
            f"| {f'{bench['max_pss_mb'] / 1024:.1f} GB' if bench else ''} "
            f"| {f'{bench['finished'].astimezone():%Y-%m-%d %H:%M}' if bench else ''} "
            f"| `logs/{log.name}` |")

    lines += ["", "## Counts", ""]
    if previous_dir:
        lines += [f"Compared with the previous build, {previous_dir.name}.", ""]
    else:
        lines += ["No earlier build with saved counts, so there is nothing to compare with.", ""]
    lines += [f"| Artifact | Count | {previous_dir.name if previous_dir else 'Previous'} "
              f"| {build_dir.name} | Change | % |",
              "|---|---|---:|---:|---:|---:|"]
    for kind, counts in stats.items():
        for metric, new in counts.items():
            old = previous.get(kind, {}).get(metric)
            diff, pct = change(old, new)
            lines.append(f"| {kind} | {metric} | {'' if old is None else f'{old:,}'} "
                         f"| {new:,} | {diff} | {pct} |")

    args.output.write_text("\n".join(lines) + "\n")
    print(args.output.read_text())


if __name__ == "__main__":
    main()
