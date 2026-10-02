"""Mirror the PubMed baseline and update files from the NCBI FTP server.

Creates the mirror on the first run and refreshes it on later runs:

    * files missing locally, or whose size or modification time differ from
      the server, are downloaded and verified against NCBI's .md5 file
    * local files that are no longer on the server are deleted, so the yearly
      baseline rollover (e.g. pubmed26n* -> pubmed27n*) does not leave stale
      files behind for build_ngd_database.py to parse twice

The local layout matches what build_ngd_database.py expects:
{dest}/ftp.ncbi.nlm.nih.gov/pubmed/{baseline,updatefiles}/
"""

import argparse
import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from ftplib import FTP, error_temp
from io import BytesIO
from pathlib import Path

HOST = "ftp.ncbi.nlm.nih.gov"
REMOTE_ROOT = "/pubmed"
SUBDIRS = ("baseline", "updatefiles")
DATA_FILE_RE = re.compile(r"^pubmed\d+n\d+\.xml\.gz$")
MD5_RE = re.compile(r"MD5\((?P<name>[^)]+)\)=\s*(?P<md5>[0-9a-f]{32})")
CHUNK_SIZE = 1024 * 1024


class Mirror:
    """Holds the FTP connection and reconnects when NCBI drops it."""

    def __init__(self, dest: Path, retries: int) -> None:
        self.local_root = dest / HOST / REMOTE_ROOT.strip("/")
        self.retries = retries
        self.ftp: FTP | None = None

    def connect(self) -> FTP:
        """Return a live FTP connection, opening a new one if needed."""
        if self.ftp is None:
            self.ftp = FTP(HOST, timeout=120)
            self.ftp.login()
        return self.ftp

    def reset(self) -> None:
        """Drop the current connection so the next call reconnects."""
        if self.ftp is not None:
            try:
                self.ftp.close()
            except OSError:
                pass
        self.ftp = None

    def with_retries(self, action, description: str):
        """Run action(ftp), reconnecting and retrying on network errors."""
        for attempt in range(1, self.retries + 1):
            try:
                return action(self.connect())
            except (OSError, EOFError, error_temp) as err:
                logging.warning("%s failed (attempt %d/%d): %s",
                                description, attempt, self.retries, err)
                self.reset()
                if attempt == self.retries:
                    raise
                time.sleep(5 * attempt)
        raise AssertionError("unreachable")

    def list_remote(self, subdir: str) -> dict[str, tuple[int, float]]:
        """Return {filename: (size, mtime)} for the data files in a remote dir."""
        def action(ftp: FTP) -> dict[str, tuple[int, float]]:
            listing = {}
            for name, facts in ftp.mlsd(f"{REMOTE_ROOT}/{subdir}",
                                        facts=["type", "size", "modify"]):
                if facts.get("type") == "file" and DATA_FILE_RE.match(name):
                    modified = datetime.strptime(facts["modify"][:14], "%Y%m%d%H%M%S")
                    listing[name] = (int(facts["size"]),
                                     modified.replace(tzinfo=timezone.utc).timestamp())
            return listing
        return self.with_retries(action, f"listing {subdir}")

    def fetch_bytes(self, remote_path: str) -> bytes:
        """Download a small remote file into memory."""
        def action(ftp: FTP) -> bytes:
            buffer = BytesIO()
            ftp.retrbinary(f"RETR {remote_path}", buffer.write)
            return buffer.getvalue()
        return self.with_retries(action, f"fetching {remote_path}")

    def download(self, subdir: str, name: str, size: int, mtime: float) -> None:
        """Download one data file, verify its md5, then move it into place."""
        local_dir = self.local_root / subdir
        target = local_dir / name
        partial = local_dir / f"{name}.part"
        remote_path = f"{REMOTE_ROOT}/{subdir}/{name}"

        md5_text = self.fetch_bytes(f"{remote_path}.md5").decode()
        match = MD5_RE.search(md5_text)
        if match is None or match["name"] != name:
            raise ValueError(f"Unexpected md5 file contents for {name}: {md5_text!r}")
        expected_md5 = match["md5"]

        local_dir.mkdir(parents=True, exist_ok=True)

        def action(ftp: FTP) -> str:
            # Fresh hash per attempt so a retried transfer is hashed from zero.
            digest = hashlib.md5()
            with open(partial, "wb") as out:
                def write(chunk: bytes) -> None:
                    out.write(chunk)
                    digest.update(chunk)
                ftp.retrbinary(f"RETR {remote_path}", write, blocksize=CHUNK_SIZE)
            return digest.hexdigest()

        for attempt in range(1, self.retries + 1):
            actual_md5 = self.with_retries(action, f"downloading {name}")
            if actual_md5 == expected_md5 and partial.stat().st_size == size:
                break
            logging.warning("Checksum mismatch for %s (attempt %d/%d)",
                            name, attempt, self.retries)
        else:
            partial.unlink(missing_ok=True)
            raise RuntimeError(f"Could not download a valid copy of {name}")

        # Stamp the server's mtime so the next run can skip this file without
        # re-hashing it.
        os.utime(partial, (mtime, mtime))
        partial.replace(target)
        (local_dir / f"{name}.md5").write_text(md5_text)


def is_current(path: Path, size: int, mtime: float) -> bool:
    """True if the local file matches the server's size and mtime and was verified."""
    if not path.exists() or not path.with_name(f"{path.name}.md5").exists():
        return False
    stat = path.stat()
    return stat.st_size == size and int(stat.st_mtime) == int(mtime)


def check_subdir(mirror: Mirror, subdir: str) -> dict:
    """Compare one local subdir with the server without changing anything."""
    local_dir = mirror.local_root / subdir
    remote = mirror.list_remote(subdir)
    if not remote:
        raise RuntimeError(f"Server listing for {subdir} is empty")
    local = ({p.name for p in local_dir.iterdir() if DATA_FILE_RE.match(p.name)}
             if local_dir.exists() else set())
    new = sorted(set(remote) - local)
    changed = sorted(name for name in set(remote) & local
                     if not is_current(local_dir / name, *remote[name]))
    stale = sorted(local - set(remote))
    return {"files": len(remote), "new": new, "changed": changed, "stale": stale}


def sync_subdir(mirror: Mirror, subdir: str) -> dict[str, int]:
    """Bring one local subdir in line with the server; return counts."""
    local_dir = mirror.local_root / subdir
    local_dir.mkdir(parents=True, exist_ok=True)

    remote = mirror.list_remote(subdir)
    if not remote:
        raise RuntimeError(f"Server listing for {subdir} is empty; refusing to sync")

    removed = 0
    for path in local_dir.iterdir():
        base = path.name.removesuffix(".md5").removesuffix(".part")
        if DATA_FILE_RE.match(base) and base not in remote:
            path.unlink()
            removed += 1
    if removed:
        logging.info("%s: removed %d stale local files", subdir, removed)

    to_fetch = sorted(name for name, (size, mtime) in remote.items()
                      if not is_current(local_dir / name, size, mtime))
    logging.info("%s: %d files on server, %d to download",
                 subdir, len(remote), len(to_fetch))

    for index, name in enumerate(to_fetch, start=1):
        size, mtime = remote[name]
        mirror.download(subdir, name, size, mtime)
        if index % 25 == 0 or index == len(to_fetch):
            logging.info("%s: downloaded %d/%d", subdir, index, len(to_fetch))

    return {"files": len(remote), "downloaded": len(to_fetch), "removed": removed}


def main() -> None:
    """CLI entry point: sync the mirror and write a manifest."""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dest", type=Path, required=True,
                        help="Mirror root; files go under {dest}/ftp.ncbi.nlm.nih.gov/pubmed/.")
    parser.add_argument("--manifest", type=Path,
                        help="Path to write the JSON summary of this sync.")
    parser.add_argument("--check", action="store_true",
                        help="Only report how the local mirror differs from the "
                             "server. Exits 0 if it is current, 1 if not.")
    parser.add_argument("--retries", type=int, default=5,
                        help="Attempts per network operation. Default: 5")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    mirror = Mirror(args.dest, args.retries)

    if args.check:
        try:
            report = {subdir: check_subdir(mirror, subdir) for subdir in SUBDIRS}
        finally:
            mirror.reset()
        outdated = False
        for subdir, diff in report.items():
            logging.info("%s: %d on server, %d new, %d changed, %d stale local",
                         subdir, diff["files"], len(diff["new"]),
                         len(diff["changed"]), len(diff["stale"]))
            for kind in ("new", "changed", "stale"):
                if diff[kind]:
                    outdated = True
                    logging.info("  %s: %s", kind, ", ".join(diff[kind][:10])
                                 + (" ..." if len(diff[kind]) > 10 else ""))
        logging.info("Mirror is %s", "OUT OF DATE" if outdated else "up to date")
        raise SystemExit(1 if outdated else 0)

    if args.manifest is None:
        parser.error("--manifest is required unless --check is given")

    started = datetime.now(timezone.utc)
    try:
        summary = {subdir: sync_subdir(mirror, subdir) for subdir in SUBDIRS}
    finally:
        mirror.reset()

    baseline_files = sorted((mirror.local_root / "baseline").glob("pubmed*n*.xml.gz"))
    manifest = {
        "host": HOST,
        "local_root": str(mirror.local_root),
        "baseline_release": baseline_files[0].name.split("n")[0] if baseline_files else None,
        "started_at": started.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        **summary,
    }
    args.manifest.write_text(json.dumps(manifest, indent=2) + "\n")
    logging.info("Wrote manifest %s", args.manifest)


if __name__ == "__main__":
    main()
