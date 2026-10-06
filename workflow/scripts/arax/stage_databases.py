#!/usr/bin/env python3
"""Stage a build's release artifacts in {root}/tier0-YYYYMMDD/ on one server.

Used for the ARAX database server (which ARAX_database_manager.py downloads
from) and for the hosts that keep their own copies (CI, arax.ncats.io). A
build is staged on a server once its files are there with the md5s the build
recorded.

The files are uploaded into a hidden staging directory and renamed into place
only after their md5s check out on the server, so the release directory never
holds partial files. An existing release directory is never overwritten: if it
already holds exactly this build's files the step succeeds without uploading,
otherwise it stops. Nothing on the server is ever deleted.

Without --upload this only reports what it would do.
"""

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path

# Upload only if the server keeps at least this much free beyond the files.
FREE_SPACE_MARGIN = 10 * 1024**3


class Stop(Exception):
    """A precondition failed; the release directory was not changed."""


def run(*args: str) -> str:
    """Run a command and return its stdout; raise Stop with its stderr on failure."""
    result = subprocess.run(args, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise Stop(f"`{shlex.join(args)}` failed: {(result.stderr or result.stdout).strip()}")
    return result.stdout


def ssh(host: str, command: str) -> str:
    return run("ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", host, command)


def remote_md5s(host: str, directory: str, names: list[str]) -> dict[str, str]:
    """md5 of each named file in a server directory; missing files are left out."""
    command = "cd {} && for f in {}; do [ -f \"$f\" ] && md5sum \"$f\"; done; true".format(
        shlex.quote(directory), " ".join(shlex.quote(n) for n in names))
    return {name: digest for digest, name in (line.split(maxsplit=1)
                                              for line in ssh(host, command).splitlines())}


def check_readable(host: str, prefix: str, directory: str, names: list[str]) -> None:
    """Every named file in directory can be read by the user prefix runs as."""
    tests = " && ".join(f"test -r {shlex.quote(f'{directory}/{name}')}" for name in names)
    try:
        ssh(host, f"{prefix} sh -c {shlex.quote(tests)}")
    except Stop as stop:
        raise Stop(f"Not readable through `{prefix}` in {directory}: {stop}") from None


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--md5-dir", type=Path, required=True,
                        help="One md5sum-format file per release artifact.")
    parser.add_argument("--release", required=True, help="Release directory name, tier0-YYYYMMDD.")
    parser.add_argument("--host", required=True, help="ssh destination, e.g. rtxconfig@arax-databases.rtx.ai")
    parser.add_argument("--root", required=True, help="Directory on the host holding releases.")
    parser.add_argument("--dir-mode", default="775",
                        help="Permissions of the release directory (default: 775).")
    parser.add_argument("--sudo", action="store_true",
                        help="Create and rename the release directory with sudo, for a root "
                             "that the ssh user cannot write to.")
    parser.add_argument("--read-check", default="",
                        help="Command prefix that runs a command as the user ARAX reads the "
                             "files as, e.g. 'sudo -n docker exec rtx2 sudo -u rt'. When set, "
                             "the step fails unless that user can read every staged file.")
    parser.add_argument("--upload", action="store_true",
                        help="Actually upload. Without it, report what would happen.")
    parser.add_argument("--result", type=Path, required=True,
                        help="JSON file to write the outcome.")
    args = parser.parse_args()

    expected = {}
    for md5_file in sorted(args.md5_dir.glob("*.md5")):
        digest, name = md5_file.read_text().split()
        expected[name] = digest
    files = [args.build_dir / name for name in expected]
    release_dir = f"{args.root.rstrip('/')}/{args.release}"
    staging_dir = f"{args.root.rstrip('/')}/.staging-{args.release}"
    sudo = "sudo -n " if args.sudo else ""

    try:
        if not expected:
            raise Stop(f"No md5 files in {args.md5_dir}")
        local = dict(line.split()[::-1] for line in run("md5sum", *map(str, files)).splitlines())
        local = {Path(path).name: digest for path, digest in local.items()}
        if local != expected:
            raise Stop("Local artifacts do not match their md5 files: "
                       + ", ".join(n for n in expected if local.get(n) != expected[n]))
        print(f"{len(files)} artifacts match their md5s; staging to {args.host}:{release_dir}")

        if ssh(args.host, f"test -e {shlex.quote(release_dir)} && echo yes || true").strip():
            staged = remote_md5s(args.host, release_dir, list(expected))
            if staged == expected:
                if args.read_check:
                    check_readable(args.host, args.read_check, release_dir, list(expected))
                print(f"{release_dir} already holds exactly these files; nothing to upload")
                args.result.write_text(json.dumps({"staged": release_dir, "uploaded": False}) + "\n")
                return
            raise Stop(f"{release_dir} already exists with different or missing files; "
                       "it is never overwritten")

        if args.read_check:
            ssh(args.host, f"{args.read_check} true")
            print(f"`{args.read_check}` works on {args.host}")

        needed = sum(f.stat().st_size for f in files)
        free = int(ssh(args.host, f"df --output=avail -B1 {shlex.quote(args.root)} | tail -1"))
        if free < needed + FREE_SPACE_MARGIN:
            raise Stop(f"Only {free / 1024**3:.0f} GB free on {args.host}; "
                       f"{needed / 1024**3:.1f} GB needed plus a margin")

        if not args.upload:
            print(f"DRY RUN: would upload {needed / 1024**3:.1f} GB to {staging_dir}, verify "
                  f"md5s there, and rename it to {release_dir}. Nothing uploaded.")
            args.result.write_text(json.dumps({"dry_run": True, "staged": release_dir}) + "\n")
            return

        # A staging directory left by an interrupted run is resumed, not restarted.
        # It belongs to the ssh user, so the upload itself never needs sudo.
        quoted = shlex.quote(staging_dir)
        ssh(args.host, f"{sudo}mkdir -p {quoted} && {sudo}chown \"$(id -u):$(id -g)\" {quoted}"
                       f" && chmod {shlex.quote(args.dir_mode)} {quoted}")
        ssh_options = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]
        if ssh(args.host, "command -v rsync || true").strip():
            run("rsync", "--partial", "--times", "--chmod=F644", "-e", shlex.join(["ssh", *ssh_options]),
                *map(str, files), f"{args.host}:{staging_dir}/")
        else:
            # Without rsync on the server, scp: an interrupted file starts over.
            print(f"No rsync on {args.host}; copying with scp")
            run("scp", "-p", "-q", *ssh_options, *map(str, files), f"{args.host}:{staging_dir}/")
            ssh(args.host, "chmod 644 " + " ".join(shlex.quote(f"{staging_dir}/{f.name}") for f in files))
        uploaded = remote_md5s(args.host, staging_dir, list(expected))
        if uploaded != expected:
            raise Stop(f"md5 mismatch after upload in {staging_dir}: "
                       + ", ".join(n for n in expected if uploaded.get(n) != expected[n]))
        # mv -T renames instead of moving into an existing directory, and fails
        # if one with files in it appeared meanwhile.
        ssh(args.host, f"{sudo}mv -T {quoted} {shlex.quote(release_dir)}")
        if remote_md5s(args.host, release_dir, list(expected)) != expected:
            raise Stop(f"md5 mismatch in {release_dir} after the rename")
        if args.read_check:
            check_readable(args.host, args.read_check, release_dir, list(expected))
        print(f"Staged {len(files)} artifacts in {args.host}:{release_dir}")
        args.result.write_text(json.dumps({"staged": release_dir, "uploaded": True}) + "\n")
    except Stop as stop:
        print(f"STOPPED: {stop}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
