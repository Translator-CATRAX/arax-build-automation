#!/usr/bin/env python3
"""Write a copy of ARAX's config_dbs.json with selected database paths updated.

Each --set KEY=FILENAME replaces database_downloads[KEY] with DB_DIR/FILENAME.
Every other entry is copied unchanged, and the template is never modified.
"""

import argparse
import json
from pathlib import Path


def main() -> None:
    """CLI entry point: update the selected entries and print what changed."""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--template", type=Path, required=True,
                        help="Existing config_dbs.json to start from.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--db-dir", required=True,
                        help="ARAX database directory for the new files, "
                             "e.g. /translator/data/orangeboard/databases/tier0-20260928")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=FILENAME",
                        dest="updates")
    args = parser.parse_args()

    config = json.loads(args.template.read_text())
    downloads = config["database_downloads"]
    for update in args.updates:
        key, filename = update.split("=", 1)
        if key not in downloads:
            raise KeyError(f"{key!r} is not in database_downloads of {args.template}")
        new_path = f"{args.db_dir.rstrip('/')}/{filename}"
        print(f"{key}: {downloads[key]} -> {new_path}")
        downloads[key] = new_path

    for key in sorted(downloads.keys() - {u.split('=', 1)[0] for u in args.updates}):
        print(f"{key}: unchanged ({downloads[key]})")

    # Same layout as the RTX file (3-space indent) so the diff stays small.
    args.output.write_text(json.dumps(config, indent=3) + "\n")


if __name__ == "__main__":
    main()
