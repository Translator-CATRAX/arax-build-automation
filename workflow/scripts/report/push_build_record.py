#!/usr/bin/env python3
"""Commit a build's record (builds/YYYYMMDD/) and push it to the autobuild repo.

This is the pipeline's last step and the only one that writes to the autobuild
repo. It runs only after the build was staged and its RTX PR opened for real
(not as dry runs), and it commits nothing but the build's record directory:

  * the checkout must be on the configured branch, with no commits that are
    not already on the remote (it may fast-forward to the remote first), so
    the push carries only the record commit;
  * the commit is limited to builds/YYYYMMDD/, whatever else is changed or
    staged in the checkout;
  * the push is a normal push of that branch, never forced.

Without --push it reports what it would commit and changes nothing.
"""

import argparse
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path


class Stop(Exception):
    """A precondition failed; nothing was committed or pushed."""


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(("git", "-C", str(repo), *args), text=True,
                            capture_output=True, check=False)
    if result.returncode != 0:
        raise Stop(f"`git {shlex.join(args)}` failed: {(result.stderr or result.stdout).strip()}")
    return result.stdout


def check_released(staged: list[Path], pr: Path) -> str:
    """The build was staged and its RTX PR opened for real; return the PR URL."""
    if not staged:
        raise Stop("No staging results")
    for path in staged:
        result = json.loads(path.read_text())
        if result.get("dry_run"):
            raise Stop(f"Staging on {path.stem} was a dry run")
    result = json.loads(pr.read_text())
    if result.get("dry_run") or not result.get("url"):
        raise Stop("The RTX PR was a dry run")
    return result["url"]


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo-dir", type=Path, required=True, help="The autobuild checkout.")
    parser.add_argument("--version", required=True, help="Graph version, YYYYMMDD.")
    parser.add_argument("--staged", type=Path, nargs="+", required=True,
                        help="Staging result files.")
    parser.add_argument("--rtx-pr", type=Path, required=True, help="RTX PR result file.")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--branch", default="master")
    parser.add_argument("--author-name", default="ARAX autobuild pipeline")
    parser.add_argument("--author-email",
                        help="Commit author email (default: the checkout's git user.email).")
    parser.add_argument("--push", action="store_true",
                        help="Actually commit and push. Without it, report what would happen.")
    parser.add_argument("--result", type=Path, required=True,
                        help="JSON file to write the outcome.")
    args = parser.parse_args()

    if not re.fullmatch(r"\d{8}", args.version):
        raise SystemExit(f"Not a graph version: {args.version!r}")
    record = f"builds/{args.version}"

    try:
        pr_url = check_released(args.staged, args.rtx_pr)
        repo = args.repo_dir
        if not (repo / record).is_dir():
            raise Stop(f"No build record at {repo / record}")
        branch = git(repo, "branch", "--show-current").strip()
        if branch != args.branch:
            raise Stop(f"The checkout is on {branch!r}, not {args.branch!r}")

        git(repo, "fetch", "--quiet", args.remote, args.branch)
        upstream = f"{args.remote}/{args.branch}"
        ahead = git(repo, "rev-list", "--count", f"{upstream}..HEAD").strip()
        if ahead != "0":
            raise Stop(f"{branch} has {ahead} commit(s) not on {upstream}; push or drop them "
                       "first so this push carries only the build record")
        behind = git(repo, "rev-list", "--count", f"HEAD..{upstream}").strip() != "0"
        if behind and args.push:
            git(repo, "merge", "--quiet", "--ff-only", upstream)

        changes = git(repo, "status", "--porcelain", "--untracked-files=all", "--", record)
        if not changes.strip():
            print(f"{record} is already committed; nothing to push")
            args.result.write_text(json.dumps({"committed": False, "record": record}) + "\n")
            return

        if not args.push:
            if behind:
                print(f"DRY RUN: would first fast-forward {branch} to {upstream}")
            print(f"DRY RUN: would commit and push these changes to {upstream}:\n{changes}")
            args.result.write_text(json.dumps({"dry_run": True, "record": record}) + "\n")
            return

        email = args.author_email or git(repo, "config", "user.email").strip()
        git(repo, "add", "--", record)
        # A pathspec commit takes only these paths, even if other changes are staged.
        git(repo, "-c", f"user.name={args.author_name}", "-c", f"user.email={email}",
            "commit", "--quiet", "-m", f"Build record for Tier0 build {args.version}",
            "-m", f"RTX PR: {pr_url}", "--", record)
        committed = git(repo, "show", "--name-only", "--format=", "HEAD").split()
        outside = [path for path in committed if not path.startswith(f"{record}/")]
        if outside:
            raise Stop(f"The record commit unexpectedly includes {outside}; it was not pushed")
        git(repo, "push", "--quiet", args.remote, f"HEAD:refs/heads/{args.branch}")
        commit = git(repo, "rev-parse", "--short", "HEAD").strip()
        print(f"Pushed {record} to {upstream} as {commit}")
        args.result.write_text(json.dumps({"committed": True, "record": record,
                                           "commit": commit}) + "\n")
    except Stop as stop:
        print(f"STOPPED: {stop}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
