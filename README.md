# autobuild

A pipeline that builds ARAX's databases from a Tier0 (translator_kg) graph release, tests them, stages them on the ARAX servers and opens a PR to RTX that points `config_dbs.json` at them. You run it with `./autobuild`, which uses Snakemake to build, and does only the actions you ask for.

It builds autocomplete, `tier0-info-for-overlay`, `curie_to_pmids`, `curie_ngd` and `fda_approved_drugs`.

For the full Tier0 rollout procedure (what each artifact is, the servers, testing, merging, production rollout and rollback), see the [RTX wiki's Rollout Procedure](https://github.com/RTXteam/RTX/wiki/Rollout-Procedure).

## Requirements

- Linux, Python 3.12, [uv](https://docs.astral.sh/uv/), git, [gh](https://cli.github.com/) (logged in), ssh, rsync, sftp, curl, zstd
- About 24 cores, 64 GB RAM and 350 GB free disk
- Passwordless SSH access to the staging servers and the DrugBank webhost listed in `config/config.yaml`
- Push access to RTXteam/RTX and to this repo
- A checkout of [RTX](https://github.com/RTXteam/RTX), whose `code/config_dbs.json` is the template for the new one

## Setup

**TL;DR - clone the repo, set up the venv, and make sure you have an RTX checkout on an up-to-date `master`.**

Clone the repo. A release needs a GitHub SSH key, so clone over SSH:

```bash
git clone git@github.com:Translator-CATRAX/arax-build-automation.git
```

Set up the virtual environment:

```bash
cd arax-build-automation
uv sync
```

`./autobuild` uses the venv by itself. To run anything else here by hand, such as `pytest` or `snakemake`, activate it first with `source .venv/bin/activate`. Without it, they fail with `No module named 'stitch'`.

Make sure you have an RTX checkout on an up-to-date `master`. It can be anywhere, because `rtx_config_dbs` below gives its path.

## Configure

**TL;DR - copy the example config and fill in your own settings.**

`config/config.yaml` holds the shared settings. Your own settings go in `config/config.local.yaml`, which git ignores and which overrides the shared file key by key. Create it from the example:

```bash
cp config/config.local.example.yaml config/config.local.yaml
```

The pipeline stops with a `Missing settings` error until you've filled in everything it needs.

Set your paths:

```yaml
graph_root: /path/to/translator_kg     # Tier0 graphs, in YYYYMMDD/ folders; downloaded if missing
babel_dir: /path/to/babel              # Babel sqlite files; downloaded if missing
pubmed_dir: /path/to/pubmed            # PubMed mirror; downloaded if missing
drugbank_dir: /path/to/drugbank        # DrugBank downloads; fetched over SSH if missing
output_root: /path/to/arax_databases   # Build output
rtx_config_dbs: /path/to/RTX/code/config_dbs.json
```

Babel is about 200 GB, and the PubMed mirror is about 70 GB. For `babel_dir` and `pubmed_dir`, if you set a location that doesn't have the files, the pipeline downloads them for you, so you can set those to whatever you want. The first download of each takes hours. The Babel download resumes if interrupted and is checked against its release's md5. To fetch Babel ahead of the build, run `snakemake -c1 {babel_dir}/FILENAME` with the venv activated, using the filename `./autobuild` reports.

`drugbank_dir` works the same way, but the download needs SSH access to `kg2webhost.rtx.ai` and only works if the webhost still has the pinned version. Local files are named `drugbank-YYYY-MM-DD.xml`.

The Tier0 graph is also downloaded if it's missing: about 3.3 GB, unpacking to about 27 GB. The server publishes no checksums, so the download is checked by its size and by unpacking it.

Choose the graph. Leave `graph_version` out to use the latest release, or set it to stay on one graph:

```yaml
graph_version: "YYYYMMDD"
```

The date comes from the release's `build_date`, and the graph's folder is named after it. `--stage`, `--pr` and `--record` without `--build` use your newest build, even if a newer graph has come out.

**Without a pin, `--build` moves to a new build as soon as a new release appears.** That includes rerunning a build that failed, so set `graph_version` for a real rollout. The run prints a `NOTE` when this happens.

The DrugBank version is pinned for everyone in `config/config.yaml`. Override it only if you mean to change it.

To stage for real, also set your ITRB login. Only ITRB can make one for you, if you do not have one. Reach out in `#devops-teamexpanderagent` in the Translator Slack workspace for help. A build-only run doesn't need it.

```yaml
staging:
  targets:
    itrb_sftp:
      host: team-expander-USERNAME@sftp.transltr.io
```

Nothing in the config starts a release. Only the flags you pass to `./autobuild` do.

## Usage

1. Decide which graph to build: the latest release, or the date in `graph_version`. It's downloaded to `{graph_root}/YYYYMMDD/` as `nodes.jsonl`, `edges.jsonl` and `graph-metadata.json` if it isn't there. Babel, DrugBank, PubMed and Biolink are fetched automatically too. Babel and Biolink use the versions the graph was built with, DrugBank uses your pinned version, and PubMed is whatever NCBI has when the mirror step runs.
2. Pull the latest `master` in your RTX checkout.
3. See the plan. This changes nothing and needs no server access:
   ```bash
   ./autobuild
   ```
4. Run it in tmux, since a full build takes about an hour once its inputs are local:
   ```bash
   ./autobuild --build                    # build and test; nothing leaves your machine
   ./autobuild --rehearse --stage --pr    # check server access and show the PR diff
   ./autobuild --stage --pr --record      # release
   ```
   Or do all of it in one step with `./autobuild --full`, which asks first.
5. Read `{output_root}/YYYYMMDD/build_report.md`, then get the RTX PR reviewed. Its URL is in `{output_root}/YYYYMMDD/rtx_pr.json`.

## Commands

**TL;DR - only the actions you pass are done. Run with no flags, it shows the build plan.**

| Command | What it does |
|---|---|
| `./autobuild` | Shows the build plan; changes nothing |
| `./autobuild --build [ARTIFACT ...]` | Builds and tests everything, or just these artifacts. Tests can't be skipped. |
| `./autobuild --stage [TARGET ...]` | Uploads to every server, or just these |
| `./autobuild --pr` | Opens the RTX PR, or updates it if it's still open |
| `./autobuild --record` | Commits and pushes `builds/YYYYMMDD/` to this repo |
| `./autobuild --full` | All four, in that order, after asking. Stops at the first failure. |
| `./autobuild --rehearse ...` | Shows the plan for the flags given and checks access; changes nothing |

Artifacts: `autocomplete`, `tier0-info-for-overlay`, `curie_to_pmids`, `curie_ngd`, `fda_approved_drugs`. Targets: `arax_databases`, `cicd`, `arax_ncats`, `itrb_sftp`.

| Option | What it does |
|---|---|
| `-y` | Doesn't ask. With missing artifacts, `--stage` stages the rest. Creating a new version still needs `--new-version`. |
| `--new-version` | With `--build ARTIFACT` on a staged build, creates the next version without asking |
| `-c N` | Cores Snakemake may use (default: all) |
| `--unlock` | Clears a stale Snakemake lock |

### What each action does when something isn't ready

- **`--stage` with missing artifacts:** it warns you and asks whether to build them, stage everything else, or cancel. Anything that hasn't passed its tests counts as missing. A server only counts as staged once it has every artifact.
- **`--pr`:** it stops unless every artifact is on `arax_databases` with a matching md5, because the PR's CI downloads them from there. It only changes the entries this pipeline builds, so other changes in `config_dbs.json` are kept.
- **`--record`:** it stops unless every server is staged and there's a PR.

| State of this build's PR | What `--pr` does |
|---|---|
| None | Opens a new PR |
| Open | Pushes to its branch, which updates the PR. A new commit means rerunning CI and the dev-machine tests. |
| Merged | Opens a new PR on `autobuild/tier0-YYYYMMDD-update-N`, if anything changed |
| Closed, not merged | Opens a new PR |

It stops if another build's autobuild PR is open, or if `master` is on a different Tier0 after a merge.

### Rebuilding an artifact

**Before a build is staged**, `./autobuild --build curie_ngd` rebuilds it in place, then reruns everything after it.

**After a build is staged**, its files can't change, and ARAX wouldn't download a new file with the same name. So the rebuild becomes the next unused version in `{output_root}/YYYYMMDD/vX.Y/`, after asking:

```bash
./autobuild --build curie_ngd     # builds and tests version 1.1, for example
./autobuild --stage               # uploads it
./autobuild --pr                  # points ARAX at it
./autobuild --record              # records it under builds/YYYYMMDD/v1.1/
```

- Existing files are never overwritten. Versions are numbered across the whole build, so a later rebuild of another artifact gets `v1.2`.
- Rebuilding `curie_to_pmids` also rebuilds `curie_ngd`, because it's built from that file. Declining cancels the rebuild.
- A `curie_to_pmids` rebuild uses the PubMed mirror as it is now, so its PubMed data can differ.
- RTX's `code/generate-db-symlinks.sh` hardcodes filenames. Update it by hand if the release uses it.

## Tests

**TL;DR - `--build` runs these automatically. A failure stops the run before anything can be staged or proposed.**

The tests check that the finished databases can be read the way ARAX, Pathfinder and xCRG read them, often with the same queries. They use local files only and don't start ARAX or contact Plover or Retriever.

| Step | Runs | Runs after | If it fails, these don't happen |
|---|---|---|---|
| `test_artifacts` | All of `tests/` except `test_config_dbs.py` | All five artifacts are built | md5s, `config_dbs.json`, report; so nothing can be staged |
| `test_config_dbs` | `tests/test_config_dbs.py` | `config_dbs.json` and md5s are written | Report; so there's nothing to propose |

All tests in a step run even if one fails, so the log shows every failure at once. There's no way to skip a failed test and continue. The built artifacts stay in `{output_root}/YYYYMMDD/`, but nothing has left your machine.

| File | Tests | What it protects against |
|---:|---:|---|
| `test_autocomplete.py` | 11 | Type-ahead breaking: wrong schema, failed prefix or substring searches, graph nodes that can't be found, empty or duplicate terms |
| `test_overlay.py` | 6 | Fisher's exact test or edge publication lookups failing: wrong schema, missing categories, counts that break the Biolink hierarchy, malformed edge keys |
| `test_curie_to_pmids.py` | 8 | Wrong NGD inputs: malformed PMID data, missing edge PMIDs, non-preferred CURIEs, major concepts with too few PMIDs, the "Male" name collision returning |
| `test_curie_ngd.py` | 9 | Wrong or unreadable NGD values: format changes, values that disagree with `curie_to_pmids`, missing rows, broken Pathfinder or xCRG reads |
| `test_fda_approved_drugs.py` | 3 | The FDA filter matching the wrong IDs: not a set of CURIEs, non-preferred IDs, known drugs missing |
| `test_config_dbs.py` | 5 | ARAX failing to start or find files: missing keys, wrong paths, missing md5s, other entries changed, leftover sqlite sidecar files |

**Passing doesn't prove the data is complete.** Many checks use samples, and PubMed parse failures don't fail the build. Counts that drop aren't tested either, so still read the build report and check the `curie_to_pmids` log.

Run them by hand against a finished build, with the venv activated:

```bash
python -m pytest tests -p no:cacheprovider --ignore tests/test_config_dbs.py \
    --build-dir {output_root}/YYYYMMDD --graph-dir {graph_root}/YYYYMMDD \
    --babel {babel_dir}/babel-YYYYMMDD.sqlite --biolink-cache-dir {output_root}/.cache/biolink

python -m pytest tests/test_config_dbs.py -p no:cacheprovider \
    --build-dir {output_root}/YYYYMMDD --rtx-config-dbs /path/to/RTX/code/config_dbs.json
```

## Failures

**TL;DR - read the failed step's log, fix the cause and run the same command again. It continues from where it stopped.**

1. Find the failed step in the output.
2. Read `{output_root}/YYYYMMDD/logs/<output name>.log`. `md5` has no log file, so its errors are in the output.
3. Fix the cause.
4. Rehearse the same command to confirm what it will do: `./autobuild --rehearse --build`.
5. Run the same command again.

For example, if `curie_ngd` runs out of memory, run `./autobuild --build -c12`. `curie_to_pmids` is already finished, so it isn't redone.

| Failure | Log or message | Fix | Rerun |
|---|---|---|---|
| A test fails because of a builder bug | `logs/test_artifacts.log` | Fix the builder script | `./autobuild --build`; rebuilds that artifact and everything after it |
| A test fails because the test is wrong | `logs/test_artifacts.log` | Fix the test | `./autobuild --build`; reruns tests onward, no rebuild |
| A step is killed, or `MemoryError` | Killed with no error | Use fewer cores (for `curie_ngd`) or free memory (`curie_to_pmids` ignores `-c`) | `./autobuild --build -c12` |
| Stale lock | `LockException` | Confirm no other run is active, then `./autobuild --unlock` | The same command |
| Setting not filled in | `Missing settings: ...` | Set it in `config/config.local.yaml` | The same command |
| Graph date has no release | `No release on ... was built on ...` | Fix `graph_version`, or remove it to use the latest | The same command |
| Not enough disk for the graph | `... needs about N GiB but ...` | Free space | `./autobuild --build`; the download resumes |
| Damaged graph download | `Couldn't unpack ...` or `should be N bytes` | None; the bad copy was deleted | `./autobuild --build` |
| RTX key missing | `... is not in database_downloads of ...` | Put RTX on an up-to-date `master`; if RTX renamed the key, update `arax_config.smk`, `test_config_dbs.py` and `BUILT_KEYS` in `open_config_pr.py` | `./autobuild --build` |
| Not enough disk for Babel | `... needs N GiB more ...` | Free space | `./autobuild --build` |
| SSH failure | `Permission denied (publickey)` | Fix the key or alias | `./autobuild --stage` |
| Server is short on space | `Only N GB free on ...` | Post in `#deployment` before deleting anything | `./autobuild --stage` |
| Corrupted upload | `md5 mismatch after upload` | None | `./autobuild --stage`; the upload resumes |
| Partial file on SFTP | `already exists at a different size` | Have the file removed from the drop | `./autobuild --stage` |
| Not on the database server | `Not staged on ... with matching md5s` | Stage first | `./autobuild --stage arax_databases`, then `--pr` |
| Another build's PR is open | `Another build's autobuild PR is still open` | Merge or close it | `./autobuild --pr` |
| Push rejected | The push isn't a fast-forward | Someone pushed at the same moment | `./autobuild --pr` |
| Record won't push | `... has N commit(s) not on origin/master` | Push or drop your own commits first | `./autobuild --record` |

## Tools

**TL;DR - `--stage` uses `stage_artifacts.py`. The others are only run by hand. Activate the venv first.**

| Tool | Use it to |
|---|---|
| `tools/stage_artifacts.py` | Stage files that aren't part of the pipeline's release, such as xDTD |
| `tools/run_test_suite.py` | Run a Translator test suite against ARAX |
| `workflow/scripts/curie_pmids/mirror_pubmed.py --check` | Compare the PubMed mirror with NCBI without changing anything |
| `workflow/scripts/report/artifact_stats.py` | Write counts for a build that has none, so the next report can compare against it |

### `stage_artifacts.py`

Copies files from `{output_root}/YYYYMMDD/` to the servers in `staging.targets`. It writes any missing md5s, skips files already there, and never overwrites. It shows its plan and asks before uploading. **It doesn't run the tests, so test the files first.**

```bash
python tools/stage_artifacts.py --tier0 YYYYMMDD --dry-run                       # show the plan only
python tools/stage_artifacts.py --tier0 YYYYMMDD                                 # everything, everywhere
python tools/stage_artifacts.py --tier0 YYYYMMDD FILE [FILE ...]                 # just these files
python tools/stage_artifacts.py --tier0 YYYYMMDD FILE --targets cicd itrb_sftp   # just these servers
```

Add `-y` to skip the prompt.

### `run_test_suite.py`

Tests real query behavior, which the pytests don't. It starts ARAX locally with this build's databases, runs the suite, grades the results and stops the server.

Before running it:

- Check out an RTX branch whose `config_dbs.json` uses this build. It stops if that file points at another release.
- Keep local copies of every database under `{output_root}`, including xDTD and COHD.

```bash
python tools/run_test_suite.py --tier0 YYYYMMDD                                       # default: sprint_6_tests.json
python tools/run_test_suite.py --tier0 YYYYMMDD --suite semantic_smoke_test_suite_CI.json
python tools/run_test_suite.py --url https://arax.ci.transltr.io/api/arax/v1.4        # a running ARAX, as a baseline
```

| Option | What it does |
|---|---|
| `--rtx PATH` | Uses another RTX checkout |
| `--tests-dir PATH` | Sets the Tests checkout location (default: next to RTX; cloned if missing) |
| `--no-pull` | Doesn't update the Tests checkout |
| `--port N` | Sets the local ARAX port (default: 5001) |
| `--maturity VALUE` | Sets ARAX's maturity (default: `staging`) |
| `--keep-server` | Leaves ARAX running afterward |

Results go to `{output_root}/YYYYMMDD/test_suites/<suite>-<time>/`: `summary.md`, `results.csv` and `results.json`. A `--url` run without `--tier0` writes to `{output_root}/test_suites/`.

### `mirror_pubmed.py --check`

Exits with 0 if the mirror is current and 1 if not:

```bash
python workflow/scripts/curie_pmids/mirror_pubmed.py --dest {pubmed_dir} --check
```

### `artifact_stats.py`

Only needed for a build made outside the pipeline:

```bash
python workflow/scripts/report/artifact_stats.py --output builds/YYYYMMDD/artifact_stats.json \
    --artifact autocomplete=PATH --artifact tier0_info_for_overlay=PATH \
    --artifact curie_to_pmids=PATH --artifact curie_ngd=PATH --artifact fda_approved_drugs=PATH
```

## Outputs

- `{output_root}/YYYYMMDD/`: the artifacts, `md5_sums/`, `config_dbs.json`, `build_report.md`, `logs/`, `test_results/`
- `builds/YYYYMMDD/` in this repo: the build's record (report, counts, logs, test results, staging and PR results)

## Layout

- `config/config.yaml`: shared settings: servers and release switches, off by default
- `config/config.local.yaml`: your own paths, graph version and ITRB login; not tracked (copy `config/config.local.example.yaml`)
- `autobuild`, `tools/autobuild_cli.py`: the command you run; it runs staging, the PR and the record
- `workflow/Snakefile`, `workflow/rules/`: the build and tests
- `workflow/scripts/`: the scripts the rules run
- `tests/`: pytest checks run against the finished artifacts
- `tools/`: the command's code and standalone scripts (staging by hand, running Translator test suites); `tools/tests/` tests the command itself
- `builds/`: one record per build
