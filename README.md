# autobuild

A Snakemake pipeline that builds ARAX's databases from a Tier0 (translator_kg) graph release, tests them, stages them on the ARAX servers and opens a PR to RTX that points `config_dbs.json` at them.

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
source .venv/bin/activate
```

Activate the venv in every new shell, including tmux sessions. The rules call bare `python`, and without the venv they fail with `No module named 'stitch'`.

Make sure you have an RTX checkout on an up-to-date `master`. It can be anywhere, because `rtx_config_dbs` below gives its path.

## Configure

**TL;DR - edit `config/config.yaml` to set your local paths, then choose how far the run should go.**

Set your paths:

```yaml
graph_root: /path/to/translator_kg     # Tier0 graphs, in YYYYMMDD/ folders; placed by hand
babel_dir: /path/to/babel              # Babel sqlite files; downloaded if missing
pubmed_dir: /path/to/pubmed            # PubMed mirror; downloaded if missing
drugbank_dir: /path/to/drugbank        # DrugBank downloads; fetched over SSH if missing
output_root: /path/to/arax_databases   # Build output
rtx_config_dbs: /path/to/RTX/code/config_dbs.json
```

Babel is about 200 GB, and the PubMed mirror is about 70 GB. For `babel_dir` and `pubmed_dir`, if you set a location that doesn't have the files, the pipeline downloads them for you, so you can set those to whatever you want. The first download of each takes hours. The Babel download resumes if interrupted and is checked against its release's md5. To fetch Babel ahead of the build, run `snakemake -c1 {babel_dir}/FILENAME`, using the filename `snakemake -n` reports.

`drugbank_dir` works the same way, but the download needs SSH access to `kg2webhost.rtx.ai` and only works if the webhost still has the pinned version. Local files are named `drugbank-YYYY-MM-DD.xml`.

The Tier0 graph is the only input you must download by hand (see Usage).

Still in config/config.yaml, you’ll need to set your ITRB login. Only ITRB can make one for  you, if you do not have one. Reach out to the devops channel in the Translator Slack workspace for help. 


```yaml
staging:
  targets:
    itrb_sftp:
      host: team-expander-USERNAME@sftp.transltr.io
```

Choose how far the run goes. Each section already has these keys, so edit them in place:

| Goal | `staging` | `rtx_pr` | `build_record` |
|---|---|---|---|
| Build and test only (use this for a first run) | `enabled: false` | `enabled: false` | `enabled: false` |
| Full release | `enabled: true`, `upload: true` | `enabled: true`, `push: true` | `enabled: true`, `push: true` |

**Warning:** the committed config has full release turned on, so a normal run stages files on the servers and opens a real RTX PR. Turn all three off until you're ready.

Pin the versions for the `translator_kg` and `drugbank_version` in the YAML. 

```yaml
graph_version: "YYYYMMDD"      # otherwise the newest complete graph is built
drugbank_version: "2023-01-04" # already pinned
```

The drugbank version is already pinned, but make sure it matches what you have locally. The graph version should be pinned to the version you want to be built. Otherwise, the pipeline defaults to the newest and complete folder in `graph_root`. 

## Usage

1. Put the Tier0 graph in `{graph_root}/YYYYMMDD/` (the graph's build date) as `nodes.jsonl`, `edges.jsonl` and `graph-metadata.json`. Babel, DrugBank, PubMed and Biolink are fetched automatically. Babel and Biolink use the versions the graph was built with, DrugBank uses your pinned version, and PubMed is whatever NCBI has when the mirror step runs.
 - Find the release to download. Use `translator_kg`, not `translator_kg_open`. In the output, `release_version` is `version_you_want` below, and the date in `build_date` gives `YYYYMMDD`:
 ```bash
curl -s https://kgx-storage.ci.transltr.io/releases/translator_kg/latest-release.json
 ```
 - The graph can be downloaded with the following command:
 ```bash
mkdir {graph_root}/YYYYMMDD && cd {graph_root}/YYYYMMDD
curl -LO https://kgx-storage.ci.transltr.io/releases/translator_kg/version_you_want/translator_kg.tar.zst 
tar --zstd -xf translator_kg.tar.zst 
rm translator_kg.tar.zst 
ls -l                                           # should show nodes.jsonl, edges.jsonl and graph-metadata.json 
 ```
2. Pull the latest `master` in your RTX checkout.
3. Do a dry run. It prints the versions it picked and the jobs it will run:
   ```bash
   snakemake -n
   ```
4. Run the pipeline in tmux, since a full build takes about an hour once its inputs are local:
   ```bash
   snakemake -c24 2>&1 | tee ~/autobuild-run.log
   ```
5. Read `{output_root}/YYYYMMDD/build_report.md`, then get the RTX PR reviewed. Its URL is in `rtx_pr.json`.

## Tests

**TL;DR - the pipeline runs these automatically. A failure stops the run before anything is staged or proposed.**

The tests check that the finished databases can be read the way ARAX, Pathfinder and xCRG read them, often with the same queries. They use local files only and don't start ARAX or contact Plover or Retriever.

| Step | Runs | Runs after | If it fails, these don't happen |
|---|---|---|---|
| `test_artifacts` | All of `tests/` except `test_config_dbs.py` | All five artifacts are built | md5s, `config_dbs.json`, report, staging, PR, build record |
| `test_config_dbs` | `tests/test_config_dbs.py` | `config_dbs.json` and md5s are written | Report, staging, PR, build record |

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

Run them by hand against a finished build:

```bash
python -m pytest tests -p no:cacheprovider --ignore tests/test_config_dbs.py \
    --build-dir {output_root}/YYYYMMDD --graph-dir {graph_root}/YYYYMMDD \
    --babel {babel_dir}/babel-YYYYMMDD.sqlite --biolink-cache-dir {output_root}/.cache/biolink

python -m pytest tests/test_config_dbs.py -p no:cacheprovider \
    --build-dir {output_root}/YYYYMMDD --rtx-config-dbs /path/to/RTX/code/config_dbs.json
```

## Failures

**TL;DR - read the failed step's log, fix the cause and run the same command again. Snakemake continues from the step that failed.**

1. Find the failed rule in Snakemake's output.
2. Read `{output_root}/YYYYMMDD/logs/<output name>.log`. `md5` and `record_build` have no log file, so their errors are in Snakemake's output.
3. Fix the cause.
4. Run `snakemake -n` to confirm where it will restart.
5. Run `snakemake -c24`.

For example, if `curie_ngd` runs out of memory, run `snakemake -c12`. `curie_to_pmids` is already finished, so it isn't redone.

| Failure | Log or message | Fix | Rerun |
|---|---|---|---|
| A test fails because of a builder bug | `logs/test_artifacts.log` | Fix the builder script | `snakemake -c24`; rebuilds that artifact and everything after it |
| A test fails because the test is wrong | `logs/test_artifacts.log` | Fix the test | `snakemake -c24`; reruns tests onward, no rebuild |
| A step is killed, or `MemoryError` | Killed with no error | Use fewer cores (for `curie_ngd`) or free memory (`curie_to_pmids` ignores `-c`) | `snakemake -c12` |
| Run interrupted | `IncompleteFilesException` | None | `snakemake -c24 --rerun-incomplete` |
| Stale lock | `LockException` | Confirm no other run is active, then `snakemake --unlock` | `snakemake -c24` |
| RTX key missing | `... is not in database_downloads of ...` | Put RTX on an up-to-date `master`; if RTX renamed the key, update `arax_config.smk` and `test_config_dbs.py` | `snakemake -c24` |
| Not enough disk for Babel | `... needs N GiB more ...` | Free space | `snakemake -c24` |
| SSH failure | `Permission denied (publickey)` | Fix the key or alias | `snakemake -c4 stage`, or `snakemake -c24` for all remaining steps |
| Server is short on space | `Only N GB free on ...` | Post in `#deployment` before deleting anything | `snakemake -c24` |
| Corrupted upload | `md5 mismatch after upload` | None | `snakemake -c24`; the upload resumes |
| Partial or extra file on SFTP | `already holds other files ... or files of the wrong size` | Have the file removed from the drop | `snakemake -c24` |
| Earlier PR still open | `An autobuild PR is still open` | Merge or close it | `snakemake -c1 rtx_config_pr` |
| Branch exists but no PR opened | `Branch autobuild/tier0-... already exists` | Don't delete it; see "Recover a half-finished PR step" in the rollout procedure | Follow that procedure |

**Run one step on purpose:**

```bash
snakemake -c24 {output_root}/YYYYMMDD/curie_ngd_v1.0_tier0-YYYYMMDD.sqlite   # build just this output
snakemake -c1 {output_root}/YYYYMMDD/staged/cicd.json                        # stage on one server
snakemake -c24 --forcerun curie_ngd                                          # redo a step and everything after it
```

**Once a build is staged, don't rebuild its artifacts through Snakemake.** The same filename would have a different md5, so staging stops. Build a `v1.1` by hand, stage it with `tools/stage_artifacts.py`, and open a PR that points ARAX at it.

## Useful commands

```bash
snakemake -n                                   # dry run
snakemake -c24                                 # full build
snakemake -c24 --forcerun curie_ngd            # rebuild one artifact
snakemake -c4 stage                            # stage only
snakemake -c1 rtx_config_pr                    # open the RTX PR only
snakemake --unlock                             # clear a stale lock
```

## Tools

**TL;DR - these are run by hand. The pipeline doesn't use them, and they don't affect whether it passes. Activate the venv first.**

| Tool | Use it to |
|---|---|
| `tools/stage_artifacts.py` | Stage files outside a build, such as xDTD or a `v1.1` |
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

- `config/config.yaml`: paths, servers and switches
- `workflow/Snakefile`, `workflow/rules/`: the pipeline
- `workflow/scripts/`: the scripts the rules run
- `tests/`: pytest checks run against the finished artifacts
- `tools/`: standalone scripts (staging outside a build, running Translator test suites)
- `builds/`: one record per build
