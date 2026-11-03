"""Find and fetch a Tier0 (translator_kg) graph release.

A graph's directory is {graph_root}/YYYYMMDD/, named after the date in its
release's build_date. With graph_version unset the build uses the latest release;
with it set, that date's release. A complete local directory is used as it is.

The Snakefile imports this at parse time to pick the graph (nothing is
downloaded), and the fetch rule runs it as a CLI to download one that isn't local.
The server publishes no checksums, so a download is checked by its size and by
unpacking it: a damaged archive fails to decompress.
"""

import argparse
import json
import logging
import re
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

GRAPH_FILES = ("nodes.jsonl", "edges.jsonl", "graph-metadata.json")
ARCHIVE = "translator_kg.tar.zst"
# Unpacked size relative to the archive, from releases 1.0.2 and 1.0.4 (about 8.5x).
UNPACKED_RATIO = 10


@dataclass(frozen=True)
class Release:
    name: str     # the release's folder on the server, e.g. 1.0.4
    version: str  # YYYYMMDD
    url: str      # the release's folder URL, with a trailing slash


def _get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as response:
        return response.read()


def _release_from_metadata(metadata: dict, name: str, url: str) -> Release:
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", metadata.get("build_date") or "")
    if not match:
        raise ValueError(f"Release {name} has no usable build_date")
    return Release(name, "".join(match.groups()), url)


def latest_release(releases_url: str) -> Release:
    """The release the server marks as latest."""
    root = releases_url.rstrip("/")
    metadata = json.loads(_get(f"{root}/latest-release.json"))
    name = metadata["release_version"]
    return _release_from_metadata(metadata, name, f"{root}/{name}/")


def _release_folders(index_html: str) -> list[str]:
    """Folders in a listing, newest first: dotted versions, then dated ones."""
    names = {name for name in re.findall(r'href="[^"]*/([^/"]+)/"', index_html) if name != "latest"
             and re.match(r"\d", name)}
    return sorted(names, key=lambda n: [int(part) for part in re.split(r"[._]", n) if part.isdigit()],
                  reverse=True)


def find_release(releases_url: str, version: str) -> Release:
    """The newest release built on the date version (YYYYMMDD)."""
    root = releases_url.rstrip("/")
    folders = _release_folders(_get(f"{root}/").decode())
    for name in folders:
        url = f"{root}/{name}/"
        try:
            release = _release_from_metadata(json.loads(_get(f"{url}release-metadata.json")), name, url)
        except (OSError, ValueError, KeyError):
            continue
        if release.version == version:
            return release
    raise ValueError(f"No release on {root} was built on {version}; "
                     f"releases there: {', '.join(folders)}")


def is_complete(directory: Path) -> bool:
    return all((directory / name).exists() for name in GRAPH_FILES)


def local_versions(root: Path) -> list[str]:
    """Complete YYYYMMDD graph directories under root, oldest first."""
    if not root.is_dir():
        return []
    return sorted(path.name for path in root.iterdir()
                  if re.fullmatch(r"\d{8}", path.name) and is_complete(path))


def resolve(graph_root: Path, pinned, releases_url: str) -> tuple[str, Release | None]:
    """The graph to build, and the release to fetch it from when it isn't local."""
    if pinned:
        version = str(pinned)
        if not re.fullmatch(r"\d{8}", version):
            raise ValueError(f"graph_version must be YYYYMMDD, not {version!r}; "
                             "or leave it unset to use the latest release")
        if is_complete(graph_root / version):
            print(f"Tier0: using {version} from {graph_root / version}", file=sys.stderr)
            return version, None
    else:
        try:
            release = latest_release(releases_url)
        except OSError as err:
            local = local_versions(graph_root)
            if not local:
                raise
            logging.warning("Tier0: can't reach %s, using newest local graph (%s)", releases_url, err)
            print(f"Tier0: using {local[-1]} from {graph_root / local[-1]}", file=sys.stderr)
            return local[-1], None
        if is_complete(graph_root / release.version):
            print(f"Tier0: using latest release {release.name} ({release.version}) "
                  f"from {graph_root / release.version}", file=sys.stderr)
            return release.version, None
        print(f"Tier0: using latest release {release.name} ({release.version}) "
              f"(will download {release.url})", file=sys.stderr)
        return release.version, release
    release = find_release(releases_url, version)
    print(f"Tier0: using {version} from release {release.name} (will download {release.url})",
          file=sys.stderr)
    return version, release


def remote_metadata(release: Release) -> dict:
    """A release's graph-metadata.json, without downloading the graph."""
    return json.loads(_get(f"{release.url}graph-metadata.json"))


def remote_size(url: str) -> int:
    request = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(request, timeout=30) as response:
        return int(response.headers["Content-Length"])


def fetch(release_url: str, dest: Path) -> None:
    """Download and unpack a release into dest. Everything happens beside dest, and
    the files move in only once they're all there; nothing is overwritten."""
    release_url = release_url.rstrip("/") + "/"
    dest.mkdir(parents=True, exist_ok=True)
    if conflicts := [name for name in GRAPH_FILES if (dest / name).exists()]:
        raise RuntimeError(f"{dest} already has {', '.join(conflicts)}; move them away to download "
                           "this graph again")
    work = dest.with_name(f".{dest.name}.download")
    work.mkdir(exist_ok=True)
    archive = work / ARCHIVE
    url = release_url + ARCHIVE

    size = remote_size(url)
    have = archive.stat().st_size if archive.exists() else 0
    needed = (size - have) + size * UNPACKED_RATIO
    free = shutil.disk_usage(work).free
    if needed > free:
        raise RuntimeError(f"{url} needs about {needed / 2**30:.0f} GiB but {work} has "
                           f"{free / 2**30:.0f} GiB free")
    logging.info("downloading %s (%.1f GiB, %.1f GiB already present)", url, size / 2**30, have / 2**30)
    subprocess.run(["curl", "--fail", "--location", "--silent", "--show-error", "--retry", "10",
                    "--continue-at", "-", "--output", str(archive), url], check=True)
    if (got := archive.stat().st_size) != size:
        archive.unlink()
        raise RuntimeError(f"{url} should be {size} bytes but {got} arrived; deleted the bad copy")

    logging.info("unpacking %s", archive)
    # -m: stamped now, so the files are newer than anything built from an earlier graph.
    result = subprocess.run(["tar", "--zstd", "-xmf", str(archive), "-C", str(work)],
                            capture_output=True, text=True, check=False)
    if result.returncode != 0:
        archive.unlink()
        raise RuntimeError(f"Couldn't unpack {url}: {result.stderr.strip()}; deleted the bad copy")
    if not (work / "graph-metadata.json").exists():
        (work / "graph-metadata.json").write_bytes(_get(release_url + "graph-metadata.json"))
    if missing := [name for name in GRAPH_FILES if not (work / name).exists()]:
        raise RuntimeError(f"{url} has no {', '.join(missing)}")
    for name in GRAPH_FILES:
        (work / name).rename(dest / name)
    shutil.rmtree(work)
    logging.info("graph ready in %s", dest)


def main() -> None:
    """CLI entry point used by the fetch_graph rule."""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--release-url", required=True, help="The release's folder URL.")
    parser.add_argument("--dest", type=Path, required=True, help="{graph_root}/YYYYMMDD")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    fetch(args.release_url, args.dest)


if __name__ == "__main__":
    main()
