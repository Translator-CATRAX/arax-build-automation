#!/usr/bin/env python3
"""Stage a build's release artifacts on the ITRB SFTP drop (sftp.transltr.io).

The drop holds databases/tier0-YYYYMMDD/ with the artifacts and
md5_sums/tier0-YYYYMMDD/ with one md5sum-format <artifact>.md5 per artifact.
ITRB takes them from there once the build's RTX PR is merged into master.

The server is SFTP only (S3 behind it): there is no shell, so nothing can be
checksummed or renamed in place there. Instead each uploaded file is checked
by size, and the artifacts go up before their md5 files, so a release whose
md5 files are all there is complete.

Files are never overwritten and nothing is deleted. A release directory that
already holds some of this build's files at the right sizes (an interrupted
run) is completed; one holding anything else stops the step.

Without --upload this only reports what it would do.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

SFTP_OPTIONS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]


class Stop(Exception):
    """A precondition failed; nothing on the server was changed by this step."""


def sftp(host: str, commands: list[str]) -> subprocess.CompletedProcess:
    """Run sftp batch commands. Commands prefixed with - may fail without
    aborting the batch."""
    return subprocess.run(["sftp", *SFTP_OPTIONS, "-b", "-", host],
                          input="\n".join(commands) + "\n", text=True,
                          capture_output=True, check=False)


def remote_sizes(host: str, directory: str) -> dict[str, int] | None:
    """Size of each file in a server directory, or None if it does not exist."""
    result = sftp(host, [f"-ls -l {directory}"])
    if result.returncode != 0:
        raise Stop(f"sftp {host} failed: {result.stderr.strip()}")
    if "not found" in result.stderr or "No such file" in result.stderr:
        return None
    sizes = {}
    for line in result.stdout.splitlines():
        fields = line.split(maxsplit=8)
        if line.startswith("sftp>") or len(fields) < 9:
            continue
        if fields[0].startswith("d"):
            raise Stop(f"Unexpected directory {fields[8]} in {host}:{directory}")
        sizes[fields[8].rsplit("/", 1)[-1]] = int(fields[4])
    return sizes


def check_existing(host: str, directory: str, expected: dict[str, int]) -> dict[str, int]:
    """Files of expected already in directory at the right size. Stops if the
    directory holds anything else."""
    sizes = remote_sizes(host, directory) or {}
    foreign = sorted(set(sizes) - set(expected))
    wrong = sorted(n for n in set(sizes) & set(expected) if sizes[n] != expected[n])
    if foreign or wrong:
        raise Stop(f"{host}:{directory} already holds other files ({', '.join(foreign) or 'none'}) "
                   f"or files of the wrong size ({', '.join(wrong) or 'none'}); "
                   "it is never overwritten")
    return sizes


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--md5-dir", type=Path, required=True,
                        help="One md5sum-format file per release artifact.")
    parser.add_argument("--release", required=True, help="Release directory name, tier0-YYYYMMDD.")
    parser.add_argument("--host", required=True,
                        help="sftp destination, e.g. team-expander-hodgesf@sftp.transltr.io")
    parser.add_argument("--databases", default="databases",
                        help="Directory on the server holding releases' artifacts.")
    parser.add_argument("--md5-sums", default="md5_sums",
                        help="Directory on the server holding releases' md5 files.")
    parser.add_argument("--upload", action="store_true",
                        help="Actually upload. Without it, report what would happen.")
    parser.add_argument("--result", type=Path, required=True,
                        help="JSON file to write the outcome.")
    args = parser.parse_args()

    md5_files = sorted(args.md5_dir.glob("*.md5"))
    artifacts = [args.build_dir / md5_file.read_text().split()[1] for md5_file in md5_files]
    data_dir = f"{args.databases.rstrip('/')}/{args.release}"
    md5_dir = f"{args.md5_sums.rstrip('/')}/{args.release}"
    # Artifacts first, then their md5 files.
    targets = ([(path, data_dir) for path in artifacts]
               + [(path, md5_dir) for path in md5_files])

    try:
        if not md5_files:
            raise Stop(f"No md5 files in {args.md5_dir}")
        if missing := [str(path) for path in artifacts if not path.exists()]:
            raise Stop(f"Missing artifacts: {', '.join(missing)}")

        present = {}
        for directory in (data_dir, md5_dir):
            expected = {path.name: path.stat().st_size for path, d in targets if d == directory}
            present[directory] = check_existing(args.host, directory, expected)
        todo = [(path, d) for path, d in targets if path.name not in present[d]]
        if not todo:
            print(f"{args.host}:{data_dir} and {md5_dir} already hold exactly these files; "
                  "nothing to upload")
            args.result.write_text(json.dumps({"staged": True, "location": f"{args.host}:{data_dir}",
                                               "already_staged": True}) + "\n")
            return

        size = sum(path.stat().st_size for path, _ in todo)
        plan = "\n".join(f"  {path.name} -> {d}/" for path, d in todo)
        if not args.upload:
            print(f"DRY RUN: would upload {len(todo)} files ({size / 1024**3:.1f} GB) to "
                  f"{args.host}:\n{plan}\nNothing uploaded.")
            args.result.write_text(json.dumps({"staged": False, "dry_run": True,
                                               "location": f"{args.host}:{data_dir}"}) + "\n")
            return

        print(f"Uploading {len(todo)} files ({size / 1024**3:.1f} GB) to {args.host}:\n{plan}")
        commands = [f"-mkdir {data_dir}", f"-mkdir {md5_dir}"]
        commands += [f'put "{path}" "{d}/{path.name}"' for path, d in todo]
        result = sftp(args.host, commands)
        print(result.stdout)
        if result.returncode != 0:
            raise Stop(f"upload failed: {result.stderr.strip()}")

        for directory in (data_dir, md5_dir):
            expected = {path.name: path.stat().st_size for path, d in targets if d == directory}
            if remote_sizes(args.host, directory) != expected:
                raise Stop(f"{args.host}:{directory} does not hold exactly the expected files "
                           "at the expected sizes after the upload")
        print(f"Staged {len(artifacts)} artifacts and their md5 files on {args.host}")
        args.result.write_text(json.dumps({"staged": True, "location": f"{args.host}:{data_dir}",
                                           "already_staged": False}) + "\n")
    except Stop as stop:
        print(f"STOPPED: {stop}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
