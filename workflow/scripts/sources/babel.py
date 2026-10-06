"""Find and fetch the Babel sqlite a Tier0 graph was built with.

The graph records its Babel release in graph-metadata.json (babelVersion, e.g.
"2026jul22"). The sqlite built from that release is published as a GitHub
release of Translator-CATRAX/stitch-proj, whose body names the Babel source
distribution (.../babel_outputs/2026jul22/), the sqlite's download URL and its
MD5. A release can be patched (babel-20250901-p1, -p2, ...); the highest patch
is used, so a local copy of an older patch is superseded by a newer one.

The Snakefile imports this at parse time to pick the sqlite (the highest patch
of the graph's Babel version, local or on GitHub; nothing is downloaded), and
the fetch rule runs it as a CLI to download the sqlite when it is not local.
"""

import argparse
import hashlib
import json
import logging
import re
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

GITHUB_API = "https://api.github.com/repos/{repo}/releases?per_page=100"
SOURCE_RE = re.compile(r"babel_outputs/([0-9a-z]+)/")
SQLITE_URL_RE = re.compile(r"Sqlite database file:\s*(https://\S+\.sqlite)")
MD5_RE = re.compile(r"Sqlite database MD5 hash:\s*`([0-9a-f]{32})`")
PATCH_RE = re.compile(r"-p(\d+)\.sqlite$")


@dataclass(frozen=True)
class BabelRelease:
    tag: str
    url: str
    md5: str

    @property
    def filename(self) -> str:
        return self.url.rsplit("/", 1)[1]


def babel_date(babel_version: str) -> str:
    """YYYYMMDD of a babelVersion like "2026jul22" (or "2025sep1")."""
    try:
        return datetime.strptime(babel_version, "%Y%b%d").strftime("%Y%m%d")
    except ValueError:
        raise ValueError(f"Unrecognized babelVersion {babel_version!r}; "
                         "expected the form 2026jul22") from None


def patch_level(name: str) -> int:
    """0 for babel-YYYYMMDD.sqlite, N for babel-YYYYMMDD-pN.sqlite."""
    match = PATCH_RE.search(name)
    return int(match[1]) if match else 0


# ---------------------------------------------------------------------
# Version discovery
# ---------------------------------------------------------------------

def local_sqlite(directory: Path, babel_version: str) -> Path | None:
    """Highest-patch local sqlite for babel_version, or None."""
    date = babel_date(babel_version)
    found = [path for path in directory.glob(f"babel-{date}*.sqlite")
             if re.fullmatch(rf"babel-{date}(-p\d+)?\.sqlite", path.name)]
    return max(found, key=lambda path: patch_level(path.name), default=None)


def github_release(repo: str, babel_version: str) -> BabelRelease:
    """Highest-patch stitch-proj release built from babel_version."""
    with urllib.request.urlopen(GITHUB_API.format(repo=repo), timeout=30) as response:
        releases = json.load(response)
    matches = []
    for release in releases:
        body = release.get("body") or ""
        source = SOURCE_RE.search(body)
        if release.get("draft") or not source or source[1] != babel_version:
            continue
        url, md5 = SQLITE_URL_RE.search(body), MD5_RE.search(body)
        if not (url and md5):
            raise ValueError(f"{repo} release {release['tag_name']} is for Babel "
                             f"{babel_version} but names no sqlite URL and MD5")
        matches.append(BabelRelease(release["tag_name"], url[1], md5[1]))
    if not matches:
        raise ValueError(f"No {repo} release was built from Babel {babel_version}; "
                         f"see https://github.com/{repo}/releases")
    return max(matches, key=lambda release: patch_level(release.filename))


def resolve(directory: Path, babel_version: str, repo: str) -> tuple[Path, BabelRelease | None]:
    """The sqlite to build with: the highest patch built from babel_version,
    whether local or on GitHub. Also returns the release to fetch it from when
    it is not local (None when it is). Falls back to the highest local patch
    when GitHub is unreachable."""
    local = local_sqlite(directory, babel_version)
    try:
        release = github_release(repo, babel_version)
    except OSError as err:
        if not local:
            raise
        logging.warning("Babel: GitHub unavailable, using newest local patch (%s)", err)
        release = None
    if local and (not release or patch_level(local.name) >= patch_level(release.filename)):
        print(f"Babel: using {babel_version} from {local}", file=sys.stderr)
        return local, None
    path = directory / release.filename
    print(f"Babel: using {babel_version} from {repo} release {release.tag} "
          f"(will download {release.url})", file=sys.stderr)
    return path, release


# ---------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------

def remote_size(url: str) -> int:
    request = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(request, timeout=30) as response:
        return int(response.headers["Content-Length"])


def md5sum(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 24), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(url: str, md5: str, dest: Path) -> None:
    """Download url to dest via dest.part (resuming a partial download),
    and move it into place only once its MD5 matches the release's."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    size = remote_size(url)
    have = part.stat().st_size if part.exists() else 0
    free = shutil.disk_usage(dest.parent).free
    if size - have > free:
        raise RuntimeError(f"{url} needs {(size - have) / 2**30:.1f} GiB more but "
                           f"{dest.parent} has {free / 2**30:.1f} GiB free")
    logging.info("downloading %s (%.1f GiB, %.1f GiB already present)",
                 url, size / 2**30, have / 2**30)
    subprocess.run(["curl", "--fail", "--location", "--silent", "--show-error",
                    "--retry", "10", "--continue-at", "-", "--output", str(part), url],
                   check=True)
    logging.info("checking md5 of %s", part)
    if (actual := md5sum(part)) != md5:
        part.unlink()
        raise RuntimeError(f"md5 mismatch for {url}: got {actual}, release says {md5}; "
                           "deleted the bad copy")
    part.rename(dest)
    logging.info("md5 ok: %s", dest)


def main() -> None:
    """CLI entry point used by the fetch_babel rule."""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", required=True, help="Download URL of the sqlite.")
    parser.add_argument("--md5", required=True, help="MD5 the release gives for it.")
    parser.add_argument("--dest", type=Path, required=True, help="Local sqlite path.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    fetch(args.url, args.md5, args.dest)


if __name__ == "__main__":
    main()
