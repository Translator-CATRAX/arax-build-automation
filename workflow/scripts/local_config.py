"""The settings everyone must supply themselves, and loading the merged config.

config/config.yaml holds what is shared; config/config.local.yaml (not tracked)
holds each person's own settings and overrides it key by key. The Snakefile
merges them with Snakemake's configfile, and the tools with load_config.
"""

from pathlib import Path

REQUIRED_PATHS = ("graph_root", "babel_dir", "pubmed_dir", "drugbank_dir", "output_root",
                  "rtx_config_dbs")
PLACEHOLDERS = ("/path/to", "USERNAME", "YYYYMMDD")
SETUP_HINT = ("Set them in config/config.local.yaml; copy config/config.local.example.yaml "
              "to start.")


def _is_set(value) -> bool:
    """Whether value was filled in, not left empty or as the example's placeholder."""
    return bool(value) and not any(text in str(value) for text in PLACEHOLDERS)


def missing_settings(config: dict, keys=REQUIRED_PATHS, targets=()) -> list[str]:
    """The named settings, and the hosts of the named staging targets, not yet set."""
    missing = [key for key in keys if not _is_set(config.get(key))]
    staging_targets = config.get("staging", {}).get("targets", {})
    missing += [f"staging.targets.{target}.host" for target in targets
                if not _is_set(staging_targets.get(target, {}).get("host"))]
    return missing


def _merge(base: dict, override: dict) -> dict:
    """base with override applied key by key, as Snakemake merges config files."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(repo: Path) -> dict:
    """config.yaml with config.local.yaml on top; exits if a required path is unset."""
    import yaml  # not imported at module level: the Snakefile imports this file too
    config = yaml.safe_load((repo / "config" / "config.yaml").read_text())
    local = repo / "config" / "config.local.yaml"
    if local.exists():
        config = _merge(config, yaml.safe_load(local.read_text()) or {})
    if missing := missing_settings(config):
        raise SystemExit(f"Missing settings: {', '.join(missing)}. {SETUP_HINT}")
    return config
