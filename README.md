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

## Install

```bash
git clone git@github.com:Translator-CATRAX/arax-build-automation.git
cd arax-build-automation
uv sync
export PATH=$PWD/.venv/bin:$PATH
```

Keep `.venv/bin` first on PATH whenever you run anything here. The rules call bare `python`, and without the venv they fail with `No module named 'stitch'`.

## Configure

Edit `config/config.yaml`. Each key is documented in the file. At minimum, set:

- `graph_root`, `babel_dir`, `pubmed_dir`, `drugbank_dir`, `output_root`: local directories
- `rtx_config_dbs`: your RTX checkout's `code/config_dbs.json`
- `staging.targets.itrb_sftp.host`: your own `team-expander-USERNAME@sftp.transltr.io` login

The release steps have on/off and dry-run switches:

| Section | `enabled` | dry-run switch |
|---|---|---|
| `staging` | stage on the servers | `upload: false` |
| `rtx_pr` | open the RTX PR | `push: false` |
| `build_record` | commit `builds/YYYYMMDD/` to this repo | `push: false` |

Turn all three off to only build and test.

## Usage

1. Put the Tier0 graph in `{graph_root}/YYYYMMDD/` (the graph's build date) as `nodes.jsonl`, `edges.jsonl` and `graph-metadata.json`. Babel, DrugBank, PubMed and Biolink are fetched automatically, at the versions the graph was built with.
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

If a run fails, read the failed step's log in `{output_root}/YYYYMMDD/logs/`, fix the cause and rerun the same command. Finished steps are not redone. After an interruption, add `--rerun-incomplete`.

## Useful commands

```bash
snakemake -n                                   # dry run
snakemake -c24                                 # full build
snakemake -c24 --forcerun curie_ngd            # rebuild one artifact
snakemake -c4 stage                            # stage only
snakemake -c1 rtx_config_pr                    # open the RTX PR only
snakemake --unlock                             # clear a stale lock
```

To run the tests by hand against a finished build:

```bash
python -m pytest tests -p no:cacheprovider --ignore tests/test_config_dbs.py \
    --build-dir {output_root}/YYYYMMDD --graph-dir {graph_root}/YYYYMMDD \
    --babel {babel_dir}/babel-YYYYMMDD.sqlite --biolink-cache-dir {output_root}/.cache/biolink
```

## Tools

Run these with `.venv/bin` on PATH, like everything else here.

Stage artifacts outside a build run, for example a file added to the build directory by hand. With no files named, it stages every artifact in `{output_root}/YYYYMMDD/` to every target in `staging.targets`. It writes any missing `md5_sums/*.md5`, skips files a server already has, never overwrites, and asks before uploading:

```bash
python tools/stage_artifacts.py --tier0 YYYYMMDD --dry-run                  # show the plan only
python tools/stage_artifacts.py --tier0 YYYYMMDD                            # everything, everywhere
python tools/stage_artifacts.py --tier0 YYYYMMDD FILE [FILE ...]            # just these files
python tools/stage_artifacts.py --tier0 YYYYMMDD FILE --targets cicd itrb_sftp
```

Run a Translator test suite (default `sprint_6_tests.json` from a checkout of [NCATSTranslator/Tests](https://github.com/NCATSTranslator/Tests) next to RTX) against ARAX. It starts ARAX locally from your RTX checkout's current branch with the build's databases, runs the suite, and stops the server. Check out the RTX branch whose `config_dbs.json` uses the build first. Results go to `{output_root}/YYYYMMDD/test_suites/`:

```bash
python tools/run_test_suite.py --tier0 YYYYMMDD
python tools/run_test_suite.py --tier0 YYYYMMDD --suite semantic_smoke_test_suite_CI.json
python tools/run_test_suite.py --url https://arax.ci.transltr.io/api/arax/v1.4   # a running ARAX, as a baseline
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
