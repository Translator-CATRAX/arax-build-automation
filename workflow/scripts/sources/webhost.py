"""Find and fetch the newest DrugBank release from the webhost.

The Snakefile imports this at parse time to pick which DrugBank version to
build against (remote listing only, nothing is downloaded), and the fetch
rule runs it as a CLI to download a version that is missing locally.

Versions are compared by the exported-on date in the drugbank.xml root element.
"""

import argparse
import hashlib
import logging
import re
import subprocess
import sys
from pathlib import Path

DRUGBANK_FILE_RE = re.compile(r"drugbank-(\d{4}-\d{2}-\d{2})\.xml$")
DRUGBANK_EXPORT_RE = re.compile(r'exported-on="(\d{4}-\d{2}-\d{2})"')
SSH_OPTIONS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]


class WebhostUnavailable(RuntimeError):
    """Raised when the webhost cannot be reached or answers unexpectedly."""


def ssh(host: str, command: str) -> str:
    """Run a command on the webhost and return its stdout."""
    try:
        result = subprocess.run(["ssh", *SSH_OPTIONS, host, command],
                                capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as err:
        raise WebhostUnavailable(f"ssh {host}: {err}") from err
    if result.returncode != 0:
        raise WebhostUnavailable(f"ssh {host} {command!r}: {result.stderr.strip()}")
    return result.stdout


# ---------------------------------------------------------------------
# Version discovery
# ---------------------------------------------------------------------

def remote_drugbank_version(host: str, path: str) -> str | None:
    """exported-on date of the DrugBank XML on the webhost, or None if absent."""
    out = ssh(host, f"head -c 1000 {path} 2>/dev/null || true")
    match = DRUGBANK_EXPORT_RE.search(out)
    return match[1] if match else None


def local_drugbank_versions(directory: Path) -> set[str]:
    """DrugBank versions already downloaded locally."""
    return {m[1] for path in directory.glob("drugbank-*.xml")
            if (m := DRUGBANK_FILE_RE.search(path.name))}


def resolve(kind: str, local: set[str], fetch_remote, pinned: str | None = None) -> str:
    """Pick the version to build: the pinned one, else the newest of local and
    webhost. Falls back to local-only when the webhost is unreachable."""
    if pinned:
        return str(pinned)
    try:
        remote = fetch_remote()
    except WebhostUnavailable as err:
        logging.warning("%s: webhost unavailable, using newest local version (%s)",
                        kind, err)
        remote = set()
    candidates = local | remote
    if not candidates:
        raise RuntimeError(f"{kind}: no version found locally or on the webhost")
    chosen = max(candidates)
    source = "local" if chosen in local else "webhost (will download)"
    print(f"{kind}: using {chosen} from {source}", file=sys.stderr)
    return chosen


# ---------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------

def rsync(host: str, remote_path: str, local_path: Path) -> None:
    """Copy one file from the webhost, resuming a partial transfer if any."""
    local_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["rsync", "-a", "--partial", "-e", "ssh " + " ".join(SSH_OPTIONS),
                    f"{host}:{remote_path}", str(local_path)], check=True)


def verify_md5(host: str, remote_path: str, local_path: Path) -> None:
    """Fail unless the local copy matches the webhost file's md5."""
    expected = ssh(host, f"md5sum {remote_path}").split()[0]
    digest = hashlib.md5()
    with local_path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected:
        local_path.unlink()
        raise RuntimeError(f"md5 mismatch for {local_path}; deleted the bad copy")
    logging.info("md5 ok: %s", local_path)


def fetch_drugbank(host: str, remote_path: str, version: str, local_path: Path) -> None:
    """Download the webhost DrugBank XML and check it is the expected release."""
    if remote_drugbank_version(host, remote_path) != version:
        raise RuntimeError(f"webhost DrugBank is no longer the {version} release; rerun the build")
    rsync(host, remote_path, local_path)
    verify_md5(host, remote_path, local_path)


def main() -> None:
    """CLI entry point used by the fetch_drugbank rule."""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", required=True, help="user@host of the webhost.")
    parser.add_argument("--remote", required=True, help="Path of the XML on the webhost.")
    parser.add_argument("--version", required=True)
    parser.add_argument("--dest", type=Path, required=True, help="Local XML path.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    fetch_drugbank(args.host, args.remote, args.version, args.dest)


if __name__ == "__main__":
    main()
