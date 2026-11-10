#!/usr/bin/env python3
"""Propose a build's config_dbs.json to RTX as a pull request, or update its PR.

Nothing here ever touches RTX's master (or any other protected branch). What it
does depends on this build's PRs, on branches {branch_prefix}tier0-YYYYMMDD[-update-N]:

  * one is open: pushes to its branch, which updates the PR;
  * the newest was merged: opens a new PR on a new branch from the base, if the
    base is still on this build and anything changed;
  * it was closed unmerged, or there is none: opens a new PR.

It stops if another build's autobuild PR is open (one build at a time), or if the
base is already on a newer build. Every artifact must be staged on the database
server with a matching md5, since the PR's CI downloads them from there.

Only the entries this pipeline builds are changed, starting from the target
branch's own file, so other changes on that branch are kept.

Without --push it prints the change and what it would do, and commits, pushes and
writes nothing. It never merges, approves, deploys or deletes anything.
"""

import argparse
import json
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

# Branches the script must never write to, whatever it is configured with.
PROTECTED_BRANCHES = {"master", "main", "production", "itrb-test", "dev"}


# The database_downloads entries this pipeline builds; see arax_config.smk.
BUILT_KEYS = ("info_for_overlay_sqlite", "autocomplete", "curie_to_pmids", "curie_ngd",
              "fda_approved_drugs")


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


def autobuild_prs(repo: str, base: str, prefix: str) -> list[dict]:
    """Every PR from an autobuild branch into base, newest first."""
    prs = json.loads(run("gh", "pr", "list", "--repo", repo, "--base", base, "--state", "all",
                         "--search", f"head:{prefix}", "--limit", "200",
                         "--json", "number,headRefName,state,url,createdAt"))
    return sorted((pr for pr in prs if pr["headRefName"].startswith(prefix)),
                  key=lambda pr: pr["createdAt"], reverse=True)


def is_this_build(branch: str, head: str) -> bool:
    return head == branch or head.startswith(f"{branch}-update-")


def plan(prs: list[dict], branch: str) -> tuple[str, dict | None]:
    """What to do given every autobuild PR: ("update", the open PR), ("after merge",
    the merged PR) or ("new", None)."""
    if others := [pr for pr in prs
                  if pr["state"] == "OPEN" and not is_this_build(branch, pr["headRefName"])]:
        raise Stop("Another build's autobuild PR is still open; review it before proposing this one: "
                   + ", ".join(pr["url"] for pr in others))
    mine = [pr for pr in prs if is_this_build(branch, pr["headRefName"])]
    for pr in mine:
        if pr["state"] == "OPEN":
            return "update", pr
    for pr in mine:
        if pr["state"] == "MERGED":
            return "after merge", pr
    return "new", None


def new_branch_name(branch: str, prs: list[dict], remote_heads: set[str], first_allowed: bool) -> str:
    """branch itself if it's free, otherwise the next unused branch-update-N."""
    used = remote_heads | {pr["headRefName"] for pr in prs}
    if first_allowed and branch not in used:
        return branch
    number = 1
    while f"{branch}-update-{number}" in used:
        number += 1
    return f"{branch}-update-{number}"


def remote_heads(remote: str, branch: str) -> set[str]:
    output = run("git", "ls-remote", "--heads", remote, f"refs/heads/{branch}*")
    return {line.split("refs/heads/", 1)[1] for line in output.splitlines()}


def release_of(config: dict) -> str | None:
    """The YYYYMMDD of the Tier0 release a config_dbs.json points at."""
    match = re.search(r"/tier0-(\d{8})/", config["database_downloads"].get("info_for_overlay_sqlite", ""))
    return match[1] if match else None


def apply_built_entries(target: dict, build: dict) -> dict:
    """target with only the entries this pipeline builds taken from build."""
    downloads = target["database_downloads"]
    for key in BUILT_KEYS:
        if key not in downloads or key not in build["database_downloads"]:
            raise Stop(f"{key!r} isn't in both the target branch's config_dbs.json and this build's. "
                       "If RTX renamed it, update arax_config.smk and test_config_dbs.py.")
        downloads[key] = build["database_downloads"][key]
    return target


def check_staged(host: str, remote_dir: str, md5_dirs: list[Path]) -> None:
    """Every release artifact is on the database server with the md5 we built."""
    expected = {}
    for md5_dir in md5_dirs:
        for md5_file in sorted(md5_dir.glob("*.md5")):
            digest, name = md5_file.read_text().split()
            expected[name] = digest
    if not expected:
        raise Stop(f"No md5 files in {', '.join(map(str, md5_dirs))}")
    command = "cd {} && for f in {}; do [ -f \"$f\" ] && md5sum \"$f\"; done; true".format(
        shlex.quote(remote_dir), " ".join(shlex.quote(n) for n in expected))
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
    parser.add_argument("--md5-dir", type=Path, nargs="+", required=True,
                        help="md5_sums directories: the build's and any rebuilds'.")
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
                        help="JSON file to write the outcome (branch and PR URL); only with --push.")
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
        prs = autobuild_prs(args.repo, args.base, args.branch_prefix)
        action, existing = plan(prs, branch)
        try:
            check_staged(args.staged_host, args.staged_dir, args.md5_dir)
            print(f"All artifacts are staged on {args.staged_host}:{args.staged_dir} with matching md5s")
        except Stop as stop:
            if args.push:
                raise
            print(f"Not ready yet: {stop}")

        build_config = json.loads(args.config_dbs.read_text())
        with tempfile.TemporaryDirectory(prefix="rtx-autobuild-") as tmp:
            base_clone = Path(tmp) / "base"
            run("git", "clone", "--quiet", "--depth", "1", "--single-branch",
                "--branch", args.base, args.remote, str(base_clone))
            base_config = json.loads((base_clone / "code" / "config_dbs.json").read_text())
            on_base = release_of(base_config)
            if action != "update" and on_base and on_base > args.version:
                raise Stop(f"{args.base} is already on Tier0 {on_base}, newer than {args.version}; "
                           "a PR would point ARAX back at older data.")
            if action == "after merge" and on_base != args.version:
                raise Stop(f"{existing['url']} was merged but {args.base} is on Tier0 {on_base}, "
                           f"not {args.version}; check what happened before proposing this again.")

            if action == "update":
                target_branch, clone = existing["headRefName"], Path(tmp) / "branch"
                run("git", "clone", "--quiet", "--depth", "1", "--single-branch",
                    "--branch", target_branch, args.remote, str(clone))
            else:
                clone = base_clone
                # After a merge the branch's own name is spent, even if GitHub deleted it.
                target_branch = new_branch_name(branch, prs, remote_heads(args.remote, branch),
                                                first_allowed=action == "new")
                run("git", "switch", "--quiet", "-c", target_branch, cwd=clone)
            if not target_branch.startswith(args.branch_prefix) or target_branch in PROTECTED_BRANCHES:
                raise SystemExit(f"Refusing to use branch {target_branch!r}")

            target = clone / "code" / "config_dbs.json"
            target.write_text(json.dumps(apply_built_entries(json.loads(target.read_text()), build_config),
                                         indent=3) + "\n")
            changed = run("git", "status", "--porcelain", cwd=clone).splitlines()
            if changed and changed != [" M code/config_dbs.json"]:
                raise Stop(f"Unexpected changes in the clone: {changed}")
            diff = run("git", "diff", cwd=clone)
            title = f"Update config_dbs.json for Tier0 build {args.version}"

            if not changed:
                if not existing:
                    raise Stop(f"{args.base}'s config_dbs.json already matches this build")
                print(f"{existing['url']} already has this build's config_dbs.json; nothing to do")
                if args.push:
                    args.result.write_text(json.dumps({"dry_run": False, "branch": existing["headRefName"],
                                                       "url": existing["url"]}) + "\n")
                return

            if action == "update":
                print(f"Push this to {target_branch}, which updates {existing['url']}")
            elif action == "after merge":
                print(f"Open a new PR from {target_branch}, since {existing['url']} was merged")
            else:
                print(f"Open a new PR from {target_branch}")
            if not args.push:
                print(f"\nREHEARSAL: nothing committed or pushed. The change:\n\n{diff}")
                return

            run("git", "-c", f"user.name={args.author_name}", "-c", f"user.email={args.author_email}",
                "commit", "--quiet", "-m", title, "-m",
                "Opened by the autobuild pipeline for human review. Do not merge until the "
                "databases are verified.", "--", "code/config_dbs.json", cwd=clone)
            # Explicit refspec, never --force: a push that isn't a fast-forward fails.
            run("git", "push", "--quiet", "origin", f"HEAD:refs/heads/{target_branch}", cwd=clone)
            if action == "update":
                url = existing["url"]
                print(f"Updated {url}. A new commit means rerunning CI and the dev-machine tests "
                      "before it can merge.")
            else:
                body = (f"Automated proposal from the autobuild pipeline for Tier0 build "
                        f"{args.version}. **For human review only; the pipeline never merges.**\n\n"
                        f"All release artifacts are staged on `{args.staged_host}:{args.staged_dir}` "
                        f"with md5s matching the build.\n\n"
                        f"<details><summary>config_dbs.json diff</summary>\n\n```diff\n{diff}```\n"
                        f"</details>\n\n---\n\n{args.report.read_text()}")
                body_file = Path(tmp) / "body.md"
                body_file.write_text(body)
                url = run("gh", "pr", "create", "--repo", args.repo, "--base", args.base,
                          "--head", target_branch, "--title", title, "--body-file", str(body_file)).strip()
                print(f"Opened {url}")
            args.result.write_text(json.dumps({"dry_run": False, "branch": target_branch, "url": url}) + "\n")
    except Stop as stop:
        print(f"STOPPED, nothing pushed: {stop}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
