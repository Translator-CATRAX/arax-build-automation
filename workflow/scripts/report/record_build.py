#!/usr/bin/env python3
"""Copy a build's records into the repo, keyed by graph version.

The record holds everything about a build except the artifacts themselves:
the report, the counts the next build compares against, config_dbs.json, the
PubMed manifest, md5s, test results, benchmarks and logs, plus where the
build was staged and which RTX PR proposed it. The record's build_report.md is
the build's report with a Release section added for those last two.
"""

import argparse
import json
import shutil
from pathlib import Path

FILES = ("artifact_stats.json", "config_dbs.json", "pubmed_manifest.json")
DIRECTORIES = ("md5_sums", "test_results", "benchmarks", "logs", "staged")


def release_section(build_dir: Path) -> list[str]:
    """Report lines on staging targets and the RTX PR, from their result files."""
    lines = ["", "## Release", ""]
    staged = sorted((build_dir / "staged").glob("*.json"))
    for path in staged:
        result = json.loads(path.read_text())
        if result.get("dry_run") or not result.get("staged"):
            state = "not staged: dry run only, nothing uploaded"
        elif result.get("already_staged"):
            state = "staged; already staged before this run, verified, nothing uploaded"
        else:
            state = "staged; uploaded and verified"
        lines.append(f"- `{path.stem}`: `{result.get('location')}` ({state})")
    if not staged:
        lines.append("- Not staged by the pipeline")
    pr_path = build_dir / "rtx_pr.json"
    if pr_path.exists():
        pr = json.loads(pr_path.read_text())
        lines.append(f"- RTX PR: {pr['url']} (branch `{pr['branch']}`)" if not pr.get("dry_run")
                     else f"- RTX PR: dry run only, branch `{pr['branch']}` not pushed")
    else:
        lines.append("- RTX PR: not opened by the pipeline")
    return lines


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--record-dir", type=Path, required=True,
                        help="The build's record directory, builds/YYYYMMDD.")
    args = parser.parse_args()

    args.record_dir.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        shutil.copy2(args.build_dir / name, args.record_dir / name)
    for name in DIRECTORIES:
        if (args.build_dir / name).is_dir():
            shutil.copytree(args.build_dir / name, args.record_dir / name, dirs_exist_ok=True)
    if (args.build_dir / "rtx_pr.json").exists():
        shutil.copy2(args.build_dir / "rtx_pr.json", args.record_dir / "rtx_pr.json")

    report = (args.build_dir / "build_report.md").read_text().rstrip("\n").splitlines()
    (args.record_dir / "build_report.md").write_text(
        "\n".join(report + release_section(args.build_dir)) + "\n")
    print(f"Recorded build {args.build_dir.name} in {args.record_dir}")


if __name__ == "__main__":
    main()
