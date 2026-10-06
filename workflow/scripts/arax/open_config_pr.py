#!/usr/bin/env python3
"""Propose a build's config_dbs.json to RTX as a pull request.

Nothing here ever touches RTX's master (or any other existing branch). For
each build the script:

  1. stops if a PR from an autobuild branch is still open (one at a time),
     or if this build's branch already exists on GitHub;
  2. stops unless every release artifact is staged on the database server
     with a matching md5, since the PR's CI downloads them from there;
  3. clones RTX's base branch into a throwaway directory, creates
     {branch_prefix}tier0-YYYYMMDD from its head and writes code/config_dbs.json;
  4. with --push, commits only that file, pushes the new branch by explicit
     refspec (never forced) and opens a PR into the base branch for human
     review. Without --push it prints the change and the PR it would open, and
     commits and pushes nothing.

It never merges, approves, deploys or deletes anything.
"""

import argparse
import json
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

# Branches the script must never write to, whatever it is configured with.
PROTECTED_BRANCHES = {"master", "main", "production", "itrb-test", "dev"}


class Stop(Exception):
    """A precondition failed; nothing was pushed."""


def run(*args: str, cwd: Path | None = None, capture: bool = True) -> str:
    """Run a command and return its stdout; raise Stop with its stderr on failure."""
    result = subprocess.run(args, cwd=cwd, text=True,
                            capture_output=capture, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() if capture else ""
        raise Stop(f"`{shlex.join(args)}` failed: {detail}")
    return result.stdout if capture else ""


def check_no_open_autobuild_pr(repo: str, base: str, prefix: str) -> None:
    prs = json.loads(run("gh", "pr", "list", "--repo", repo, "--base", base, "--state", "open",
                         "--json", "number,headRefName,url", "--limit", "200"))
    open_prs = [pr for pr in prs if pr["headRefName"].startswith(prefix)]
    if open_prs:
        raise Stop("An autobuild PR is still open; review it before proposing another: "
                   + ", ".join(pr["url"] for pr in open_prs))


def check_branch_is_new(remote: str, branch: str) -> None:
    if run("git", "ls-remote", "--heads", remote, f"refs/heads/{branch}").strip():
        raise Stop(f"Branch {branch} already exists on {remote}. Delete it by hand to "
                   "propose this build again.")


def check_staged(host: str, remote_dir: str, md5_dir: Path) -> None:
    """Every release artifact is on the database server with the md5 we built."""
    expected = {}
    for md5_file in sorted(md5_dir.glob("*.md5")):
        digest, name = md5_file.read_text().split()
        expected[name] = digest
    if not expected:
        raise Stop(f"No md5 files in {md5_dir}")
    command = "cd {} && md5sum {}".format(shlex.quote(remote_dir),
                                          " ".join(shlex.quote(n) for n in expected))
    output = run("ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", host, command)
    staged = {name: digest for digest, name in (line.split() for line in output.splitlines())}
    wrong = [name for name, digest in expected.items() if staged.get(name) != digest]
    if wrong:
        raise Stop(f"Not staged on {host}:{remote_dir} with matching md5s: {', '.join(wrong)}")


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config-dbs", type=Path, required=True,
                        help="The build's tested config_dbs.json.")
    parser.add_argument("--report", type=Path, required=True,
                        help="build_report.md, used as the PR description.")
    parser.add_argument("--md5-dir", type=Path, required=True)
    parser.add_argument("--version", required=True, help="Graph version, YYYYMMDD.")
    parser.add_argument("--repo", required=True, help="GitHub repo, e.g. RTXTeam/RTX.")
    parser.add_argument("--remote", required=True, help="Git URL to clone and push.")
    parser.add_argument("--base", default="master", help="Branch the PR targets.")
    parser.add_argument("--branch-prefix", default="autobuild/")
    parser.add_argument("--staged-host", required=True,
                        help="ssh destination holding staged databases, e.g. rtxconfig@arax-databases.rtx.ai")
    parser.add_argument("--staged-dir", required=True,
                        help="Directory on that host holding this build's files.")
    parser.add_argument("--author-name", default="ARAX autobuild pipeline")
    parser.add_argument("--author-email",
                        help="Commit author email (default: this machine's git user.email).")
    parser.add_argument("--push", action="store_true",
                        help="Actually push the branch and open the PR. Without it, dry run.")
    parser.add_argument("--result", type=Path, required=True,
                        help="JSON file to write the outcome (branch, PR URL or dry run).")
    args = parser.parse_args()

    if not args.author_email:
        args.author_email = run("git", "config", "user.email").strip()
    branch = f"{args.branch_prefix}tier0-{args.version}"
    if (not args.branch_prefix or not branch.startswith(args.branch_prefix)
            or branch in PROTECTED_BRANCHES or args.branch_prefix.rstrip("/") in PROTECTED_BRANCHES):
        raise SystemExit(f"Refusing to use branch {branch!r}")
    if args.base == branch:
        raise SystemExit("The PR's base and head branch are the same")

    try:
        print(f"Branch: {branch}  ->  PR into {args.repo}:{args.base}")
        check_no_open_autobuild_pr(args.repo, args.base, args.branch_prefix)
        check_branch_is_new(args.remote, branch)
        check_staged(args.staged_host, args.staged_dir, args.md5_dir)
        print(f"All {len(list(args.md5_dir.glob('*.md5')))} artifacts are staged on "
              f"{args.staged_host}:{args.staged_dir} with matching md5s")

        with tempfile.TemporaryDirectory(prefix="rtx-autobuild-") as tmp:
            clone = Path(tmp) / "RTX"
            run("git", "clone", "--quiet", "--depth", "1", "--single-branch",
                "--branch", args.base, args.remote, str(clone))
            run("git", "switch", "--quiet", "-c", branch, cwd=clone)

            target = clone / "code" / "config_dbs.json"
            target.write_text(args.config_dbs.read_text())
            changed = run("git", "status", "--porcelain", cwd=clone).splitlines()
            if not changed:
                raise Stop(f"{args.base}'s config_dbs.json already matches this build")
            if changed != [" M code/config_dbs.json"]:
                raise Stop(f"Unexpected changes in the clone: {changed}")
            diff = run("git", "diff", cwd=clone)

            title = f"Update config_dbs.json for Tier0 build {args.version}"

            body = (f"Automated proposal from the autobuild pipeline for Tier0 build "
                    f"{args.version}. **For human review only; the pipeline never merges.**\n\n"
                    f"All release artifacts are staged on `{args.staged_host}:{args.staged_dir}` "
                    f"with md5s matching the build.\n\n"
                    f"<details><summary>config_dbs.json diff</summary>\n\n```diff\n{diff}```\n"
                    f"</details>\n\n---\n\n{args.report.read_text()}")

            if not args.push:
                print("\nDRY RUN: nothing committed or pushed. Would commit this change:\n")
                print(diff)
                print(f"as {args.author_name} <{args.author_email}>, push it as new branch "
                      f"{branch}, and open PR {title!r} into {args.base}.")
                args.result.write_text(json.dumps({"dry_run": True, "branch": branch}) + "\n")
                return

            run("git", "-c", f"user.name={args.author_name}", "-c", f"user.email={args.author_email}",
                "commit", "--quiet", "-m", title, "-m",
                "Opened by the autobuild pipeline for human review. Do not merge until the "
                "databases are verified.", "--", "code/config_dbs.json", cwd=clone)
            # Explicit refspec to a new branch; never --force.
            run("git", "push", "--quiet", "origin", f"HEAD:refs/heads/{branch}", cwd=clone)
            body_file = Path(tmp) / "body.md"
            body_file.write_text(body)
            url = run("gh", "pr", "create", "--repo", args.repo, "--base", args.base,
                      "--head", branch, "--title", title, "--body-file", str(body_file)).strip()
            print(f"Opened {url}")
            args.result.write_text(json.dumps({"dry_run": False, "branch": branch, "url": url}) + "\n")
    except Stop as stop:
        print(f"STOPPED, nothing pushed: {stop}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
