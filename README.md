# autobuild

Builds the ARAX databases from a Tier0 Translator KG release, tests them, stages them on every server ARAX reads them from, opens a PR to RTX that points config_dbs.json at the new files, and commits a record of the build to this repo.

## Contents

- [Overview](#overview)
  - [What it builds](#what-it-builds)
  - [How it works](#how-it-works)
- [Before you start](#before-you-start)
  - [What you need](#what-you-need)
  - [Install](#install)
  - [SSH access](#ssh-access)
  - [Configure](#configure)
- [Inputs](#inputs)
  - [Tier0 graph](#tier0-graph)
  - [Babel](#babel)
  - [DrugBank](#drugbank)
  - [PubMed](#pubmed)
  - [Biolink](#biolink)
- [Running a full build](#running-a-full-build)
  - [1. Dry run](#1-dry-run)
  - [2. Run it](#2-run-it)
  - [3. Watch it](#3-watch-it)
  - [4. After it finishes](#4-after-it-finishes)
  - [If it fails or is interrupted](#if-it-fails-or-is-interrupted)
  - [Steps](#steps)
  - [Release switches](#release-switches)
  - [Build outputs](#build-outputs)
- [Artifacts](#artifacts)
  - [Autocomplete](#autocomplete)
  - [tier0-info-for-overlay](#tier0-info-for-overlay)
  - [Curie_to_PMIDs](#curie_to_pmids)
  - [FDA_approved_drugs](#fda_approved_drugs)
  - [Curie_ngd](#curie_ngd)
  - [ExplainableDTD (not built here)](#explainabledtd-not-built-here)
  - [COHD (not built here)](#cohd-not-built-here)
  - [md5_sums](#md5_sums)
- [Other scripts](#other-scripts)
- [Tests](#tests)
- [Servers](#servers)
  - [arax-databases.rtx.ai](#arax-databasesrtxai)
  - [sftp.transltr.io](#sftptransltrio)
  - [arax.ncats.io](#araxncatsio)
  - [cicd.rtx.ai](#cicdrtxai)
- [Staging](#staging)
  - [Staging with the pipeline](#staging-with-the-pipeline)
  - [What happens on arax-databases, cicd and arax](#what-happens-on-arax-databases-cicd-and-arax)
  - [What happens on sftp.transltr.io](#what-happens-on-sftptransltrio)
  - [Staging results](#staging-results)
  - [The RTX PR](#the-rtx-pr)
- [Rebuilding one artifact](#rebuilding-one-artifact)
  - [If the build has not been staged yet](#if-the-build-has-not-been-staged-yet)
  - [If the build has already been staged](#if-the-build-has-already-been-staged)
  - [Build the new version](#build-the-new-version)
  - [Test it](#test-it)
  - [Write its md5](#write-its-md5)
- [Staging a single artifact](#staging-a-single-artifact)
  - [On arax-databases.rtx.ai](#on-arax-databasesrtxai)
  - [On cicd.rtx.ai](#on-cicdrtxai)
  - [On arax.ncats.io](#on-araxncatsio)
  - [On sftp.transltr.io](#on-sftptransltrio)
  - [Point ARAX at the new file](#point-arax-at-the-new-file)
- [Troubleshooting](#troubleshooting)

## Overview

### What it builds

| Artifact | Built from | Used by in ARAX |
|---|---|---|
| autocomplete | Tier0 nodes | type-ahead in the ARAX UI |
| tier0-info-for-overlay | Tier0 nodes and edges | edge publications, Fisher's exact test, Infer, Pathfinder |
| curie_to_pmids | PubMed, Babel, Tier0 edges | live NGD, xCRG |
| curie_ngd | curie_to_pmids, Tier0 edges | precomputed NGD, Pathfinder, xCRG |
| fda_approved_drugs | DrugBank, Babel | the FDA-approved filter in Expand |

ExplainableDTD and COHD are also in config_dbs.json but are not built here. Their entries are carried over unchanged. See [Artifacts](#artifacts) for what each one holds and how it is built.

### How it works

The build is a Snakemake workflow. Each step is a rule that runs one script, reads some files and writes others. Snakemake works out which steps need to run from which files are missing or older than their inputs (scripts count as inputs), so a rerun only redoes what changed, and a step that fails stops everything downstream of it.

One build is one Tier0 graph release. The graph's build date (YYYYMMDD) names everything: the build directory ({output_root}/YYYYMMDD/), the artifact filenames (e.g. curie_ngd_v1.0_tier0-YYYYMMDD.sqlite), and the release directory on every server (tier0-YYYYMMDD/).

A full build goes:

1. Get the inputs (graph by hand; Babel, DrugBank and PubMed automatically)
2. Build the 5 artifacts
3. Test them. Nothing after this runs unless every test passes.
4. Write md5s, config_dbs.json and the build report
5. Stage the artifacts on the 4 servers
6. Open a PR to RTX that points config_dbs.json at them
7. Commit and push the build record to this repo

Nothing changes for ARAX users until a person reviews and merges the RTX PR. Then ARAX instances download the new files from arax-databases.rtx.ai, and ITRB builds new images from sftp.transltr.io.

Repo layout:

- config/config.yaml: every path, server and on/off switch the build uses
- workflow/Snakefile: picks the graph, Babel and DrugBank versions and defines every output path
- workflow/rules/*.smk: one file per step
- workflow/scripts/: the scripts the rules run, grouped by artifact
- tests/: pytest suite run against the finished artifacts
- builds/YYYYMMDD/: the record of each build (report, counts, logs, test results, where it was staged, the RTX PR)

## Before you start

### What you need

Machine:

- Linux with Python 3.12, git, curl, zstd, rsync, sftp and tmux
- 24 cores or so. The build uses every core you give it.
- 64 GB of RAM or more (curie_to_pmids peaks around 17 GB, curie_ngd's workers share about 12 GB)
- About 350 GB of free disk: Babel sqlite ~185-220 GB, Tier0 graph ~32 GB unpacked, PubMed mirror ~70 GB, one build's output ~7 GB

Access (ask before you start; each one blocks a different step):

- arax-databases.rtx.ai as rtxconfig, cicd.rtx.ai as ubuntu, arax.ncats.io as yourself: contact OSU
- kg2webhost.rtx.ai as ubuntu (DrugBank): contact OSU
- sftp.transltr.io as team-expander-USERNAME: contact ITRB
- GitHub: push access to Translator-CATRAX/arax-build-automation (this repo) and RTXteam/RTX

Time: once the inputs are local, a full build takes about 45 minutes, plus staging (6-7 GB to each server). The first PubMed mirror and the first Babel download take hours.

### Install

Clone this repo and RTX (the build reads RTX's code/config_dbs.json as the template for the new one):

```
git clone git@github.com:Translator-CATRAX/arax-build-automation.git
git clone git@github.com:RTXteam/RTX.git
```

Install uv if you don't have it, then create the environment from the repo root:

```
curl -LsSf https://astral.sh/uv/install.sh | sh
cd arax-build-automation
uv sync
```

This creates .venv with snakemake, stitch-proj (local Babel lookups), biolink-helper-pkg, numpy, lxml and pytest.

Every rule runs its script with plain `python`, so .venv/bin has to be first on PATH whenever you run anything in this repo. Otherwise the scripts run with the system python and fail with `No module named 'stitch'`:

```
export PATH=$PWD/.venv/bin:$PATH
```

Log GitHub's CLI in (it opens the RTX PR):

```
gh auth login
```

### SSH access

Every host must work with your SSH key and no password prompt, because the build runs unattended. Copy your key to the hosts you were given access to:

```
ssh-copy-id rtxconfig@arax-databases.rtx.ai
ssh-copy-id ubuntu@cicd.rtx.ai
ssh-copy-id ubuntu@kg2webhost.rtx.ai
```

For sftp.transltr.io, ITRB adds your public key to your team-expander account.

arax.ncats.io is only reachable from an allowed network. Off site, either connect to the VPN or go through a jump host. The build reaches it through an SSH alias named arax, so add one to ~/.ssh/config:

```
Host arax
  HostName <arax.ncats.io address, from OSU>
  User USERNAME
  ProxyCommand ssh -W %h:%p USERNAME@<jump host, from OSU>
```

On site or on the VPN, leave out the ProxyCommand line.

Check every one before the first build. Each should print without asking for a password:

```
ssh -o BatchMode=yes rtxconfig@arax-databases.rtx.ai hostname
ssh -o BatchMode=yes ubuntu@cicd.rtx.ai hostname
ssh -o BatchMode=yes ubuntu@kg2webhost.rtx.ai hostname
ssh -o BatchMode=yes arax hostname
echo ls | sftp -o BatchMode=yes -b - team-expander-USERNAME@sftp.transltr.io
ssh -o BatchMode=yes arax "sudo -n docker exec rtx2 true" && echo "docker ok"
```

The last one checks that you can run docker on arax.ncats.io without a password, which staging needs to check that ARAX can read the files.

### Configure

Everything machine-specific is in config/config.yaml. Set these before the first build. The examples in this README use the paths below; use your own.

| Key | What it is | Example |
|---|---|---|
| graph_root | where Tier0 graph releases are unpacked, one YYYYMMDD folder each | /data/translator_kg |
| graph_version | build this graph instead of the newest (leave commented out normally) | "20260928" |
| babel_dir | where Babel sqlite files are kept | /data/babel |
| babel_releases | GitHub repo the Babel sqlites are released from | Translator-CATRAX/stitch-proj |
| pubmed_dir | the PubMed mirror | /data/pubmed |
| output_root | where builds go, one YYYYMMDD folder each | /data/arax_databases |
| webhost, webhost_drugbank | where DrugBank is pulled from | ubuntu@kg2webhost.rtx.ai, /home/ubuntu/drugbank.xml |
| drugbank_dir | where DrugBank downloads are kept | /data/drugbank |
| drugbank_version | pin a DrugBank version | "2023-01-04" |
| rtx_config_dbs | your RTX checkout's config_dbs.json | /home/USERNAME/RTX/code/config_dbs.json |
| arax_db_root | where ARAX reads databases from (do not change) | /translator/data/orangeboard/databases |
| staging.targets.itrb_sftp.host | your SFTP login | team-expander-USERNAME@sftp.transltr.io |
| staging, rtx_pr, build_record | the release switches, see [Release switches](#release-switches) | |

Create the folders:

```
mkdir -p /data/translator_kg /data/babel /data/pubmed /data/arax_databases /data/drugbank
```

## Inputs

### Tier0 graph

The only input you get by hand. Tier0 (translator_kg) releases are published at https://kgx-storage.ci.transltr.io/releases/translator_kg/ (browsable, no login). Each release is a version folder (1.0.2, 1.0.3, ...) holding:

- translator_kg.tar.zst: nodes.jsonl and edges.jsonl (~3.4 GB compressed, ~28 GB unpacked)
- graph-metadata.json: what the graph was built with, including the Babel and Biolink versions this build follows
- release-metadata.json: the release's version, build date, Babel and Biolink versions

latest-release.json in that folder names the newest release. Use translator_kg, not translator_kg_open.

1. Pick the release and read its build date:

```
curl -s https://kgx-storage.ci.transltr.io/releases/translator_kg/latest-release.json
```

The release_version (e.g. 1.0.2) is the folder to download, and the date part of build_date (2026-09-28T23:50:46Z → 20260928) is the folder name to unpack it into. That date becomes the build's version.

2. Download and unpack it:

```
mkdir /data/translator_kg/20260928
cd /data/translator_kg/20260928
curl -O https://kgx-storage.ci.transltr.io/releases/translator_kg/1.0.2/translator_kg.tar.zst
curl -O https://kgx-storage.ci.transltr.io/releases/translator_kg/1.0.2/graph-metadata.json
tar --zstd -xf translator_kg.tar.zst
rm translator_kg.tar.zst
ls -l
```

The folder must end up with exactly nodes.jsonl, edges.jsonl and graph-metadata.json. The build picks the newest complete YYYYMMDD folder in graph_root, unless graph_version in config.yaml pins another.

### Babel

Automatic. The build reads the Babel version from graph-metadata.json (translator:babelVersion, e.g. 2026jul22) and uses the Babel sqlite built from that version. Those sqlites are released at https://github.com/Translator-CATRAX/stitch-proj/releases. Each release names the Babel version it was built from, the sqlite's download link and its MD5.

- If a release was patched (babel-20250901-p1, -p2, -p3), the highest patch wins.
- If that file is already in babel_dir (named like babel-20260722.sqlite or babel-20250901-p3.sqlite), it is used as is.
- Otherwise the fetch_babel step downloads it (~185-220 GB), checks the MD5 and moves it into place. An interrupted download resumes.
- If GitHub cannot be reached, the newest local patch of that version is used.
- The build stops if no release matches the graph's Babel version.

The dry run prints which Babel file it will use and whether it will download it.

### DrugBank

Automatic. Pulled over SSH from webhost (kg2webhost.rtx.ai) into drugbank_dir as drugbank-YYYY-MM-DD.xml, named by its exported-on date. It is downloaded only when that version is not already local, and checked by md5 against the webhost copy. drugbank_version in config.yaml pins a version and skips the check; builds so far pin 2023-01-04. DrugBank is not redistributable, so it is never staged or committed anywhere.

### PubMed

Automatic. The pubmed_mirror step mirrors ftp.ncbi.nlm.nih.gov/pubmed (baseline and update files) into pubmed_dir on every build. The first run downloads all of it (~70 GB, several hours). After that it only fetches files that are new or changed. See [Curie_to_PMIDs](#curie_to_pmids) for how it works.

### Biolink

Nothing to do. The version recorded in graph-metadata.json (translator:biolinkVersion) is used, and BiolinkHelper caches it in {output_root}/.cache/biolink.

## Running a full build

All commands are run from the repo root with the venv on PATH:

```
cd arax-build-automation
export PATH=$PWD/.venv/bin:$PATH
```

### 1. Dry run

Set the [release switches](#release-switches) in config.yaml for what you want this run to do (for a full release, all three enabled with upload/push true). Then:

```
snakemake -n
```

This runs nothing. It prints the graph, Babel and Biolink versions it picked, whether it will download Babel or DrugBank, and every job it would run. For a full build from scratch the job list ends with stage_target, stage_sftp, rtx_config_pr, record_build and push_build_record.

### 2. Run it

Run it inside tmux, so it keeps going if the terminal closes or SSH drops:

```
tmux new -s autobuild
export PATH=$PWD/.venv/bin:$PATH
snakemake -c24 2>&1 | tee ~/autobuild-run.log
```

-c24 is the number of cores to use. Ctrl-b then d leaves tmux with the build still running. Ctrl-c inside tmux stops the build.

### 3. Watch it

```
tmux attach -t autobuild
```

or, without attaching:

```
tail -f ~/autobuild-run.log
```

Snakemake prints each step as it starts and finishes ("12 of 23 steps (52%) done"). Each step's own output is in /data/arax_databases/20260928/logs/, named after the file the step writes, e.g.:

```
tail -f /data/arax_databases/20260928/logs/curie_to_pmids_v1.0_tier0-20260928.sqlite.log
```

### 4. After it finishes

The last line in the log is "N of N steps (100%) done". Then:

1. Read the report, /data/arax_databases/20260928/build_report.md: inputs, test results, how long each step took, and how each artifact's counts changed since the last build. A big unexplained drop is worth a look before merging.
2. Open the RTX PR (its URL is in rtx_pr.json and in the report) and have it reviewed. When it is merged, ARAX instances pick up the new databases, and ITRB builds new images from the SFTP server.
3. The build record is already pushed to builds/20260928/ in this repo.

### If it fails or is interrupted

Snakemake stops at the first failed step and prints which one. Look at that step's log in logs/, fix the cause, and run the same command again: finished steps are not redone.

If a run was interrupted (Ctrl-c, reboot, lost session), Snakemake refuses to start again until it is told what to do with the half-written files. Add --rerun-incomplete to delete and rebuild them:

```
snakemake -c24 --rerun-incomplete
```

See [Troubleshooting](#troubleshooting) for common errors.

### Steps

In the order they run:

1. fetch_babel, fetch_drugbank: only when the needed version is not local
2. pubmed_mirror: refresh the PubMed mirror
3. autocomplete, overlay, curie_to_pmids, fda_drugs: build the artifacts (in parallel)
4. curie_ngd: build from curie_to_pmids
5. test_artifacts: pytest against every artifact
6. md5: one .md5 per artifact
7. arax_config_dbs, test_config_dbs: write and check the new config_dbs.json
8. report: build_report.md and artifact_stats.json
9. stage_target, stage_sftp: stage on the 4 servers
10. rtx_config_pr: open the RTX PR
11. record_build, push_build_record: copy the record to builds/YYYYMMDD/ and push it

### Release switches

In config.yaml. Steps 1-8 always run; these control 9-11:

- staging.enabled: stage as part of a normal build. staging.upload: false makes every server a dry run (checks and reports, uploads nothing).
- rtx_pr.enabled: open the RTX PR as part of a normal build. rtx_pr.push: false clones RTX and prints the change and the PR, and pushes nothing.
- build_record.enabled: commit and push builds/YYYYMMDD/ to this repo. build_record.push: false reports what it would commit.

With all three enabled and pushing, one `snakemake -c24` goes from the raw graph to a staged release, an open RTX PR and a pushed build record. With all three off, it builds and tests the artifacts and stops.

The record push only commits builds/YYYYMMDD/. It stops if your checkout is not on master, or has commits that are not pushed yet, so commit and push any changes of your own before the build reaches that step.

### Build outputs

In {output_root}/YYYYMMDD/:

- autocomplete_v1.0_tier0-YYYYMMDD.sqlite
- tier0-info-for-overlay_v1.0_tier0-YYYYMMDD.sqlite
- curie_to_pmids_v1.0_tier0-YYYYMMDD.sqlite
- curie_ngd_v1.0_tier0-YYYYMMDD.sqlite
- fda_approved_drugs_v1.0.pickle
- md5_sums/<artifact>.md5
- config_dbs.json: RTX's config_dbs.json with the 5 artifacts pointed at /translator/data/orangeboard/databases/tier0-YYYYMMDD/
- pubmed_manifest.json: the PubMed snapshot this build used
- test_results/: junit xml from both test runs
- staged/<server>.json: staging result per server
- rtx_pr.json: the PR's branch and URL
- build_report.md, artifact_stats.json: what was built from what, how long it took, and how each artifact's counts changed since the last build
- logs/, benchmarks/ (time and memory per artifact)

A new build always names its files v1.0. v1.1 and up are only for an artifact rebuilt within a build that was already released (see [Rebuilding one artifact](#rebuilding-one-artifact)).

## Artifacts

### Autocomplete

- Consumes: nodes.jsonl
- Contains: all node names + CURIEs (case-insensitive)
- Consumed by: autocomplete/rtxcomplete.py
- For: type-ahead suggestions in the ARAX UI

Description of code: the ARAX UI suggests node names and CURIEs as the user types, using rtxcomplete.py. That module answers those requests by running prefix and substring queries against the autocomplete database. The code builds one case-insensitive list of every node name and node CURIE in the Tier0 graph.

- extract_names_ids.py streams the nodes file and writes id<tab>name per node to a temp TSV in the build's work/ directory. It collapses runs of whitespace and skips lines that are not valid JSON.
- create_load_db.py creates the terms table (COLLATE NOCASE) and loads both the name and the CURIE from each row. It keeps only the first spelling of each term when case is ignored, merging things like "ASPIRIN", "Aspirin" and "aspirin". Empty names are skipped.
- The index (idx_terms_term) is built after the load and the TSV is deleted.

Output:

- terms(term) → ~3.6M rows
- There is no fragment cache table. rtxcomplete keeps its cache in its own rtxcomplete_cache.sqlite.

### tier0-info-for-overlay

- Consumes: nodes.jsonl, edges.jsonl, graph-metadata.json
- Contains: edge publications, neighbor counts, category counts
- Consumed by: ARAX_decorator, Overlay/fisher_exact_test.py, Infer/scripts/infer_utilities.py, Path_Finder/utility.py → ARAX_connect.py
- For: attaching PMIDs to result edges, and the counts for Fisher's exact test, used by Infer and Pathfinder
- Config keys: tier0_sqlite and kg2c_sqlite both point at this file

Description of code: several ARAX features need graph-wide stats that are too slow to compute at run time. Overlay's Fisher exact test needs neighbor counts per biolink category, and how many nodes exist in each category. This script (overlay/generate_sqlite.py) builds a small extract of the graph. It has no nodes or edges tables.

- Loads the node categories from the nodes file.
- Expands the categories to all of their biolink ancestors, including mixins, using BiolinkHelper at the Biolink version recorded in graph-metadata.json.
- Streams the edges once. For each edge that has publications, it writes a row to edge_publications. The key is the canonical ARAX edge key: subject--predicate--qualified predicate--object direction qualifier--object aspect qualifier--object--primary knowledge source. Missing qualifiers are left empty, so every key has all seven parts.
- Publications are joined by the ǂ symbol.
- Neighbors are counted for each node in each expanded category and stored as a JSON dict.

Output:

- edge_publications(arax_edge_key, publications) ~8.2M rows
- neighbors(id, neighbor_counts) ~1.77M rows
- category_counts(category, count) ~68 rows

NOTES: the edge-key logic is copied from ARAXQuery/util.get_arax_edge_key in RTX. If either copy changes, both must change together, or lookups will silently fail.

### Curie_to_PMIDs

- Consumes: PubMed mirror, Babel, edges.jsonl (publications)
- Contains: CURIE → sorted int32 PMID array
- Consumed by: Overlay/compute_ngd.py, ARAX_connect.py (xCRG)
- For: computing NGD (how often two concepts appear in the same papers) on the fly, and connecting concepts

Description of code: this artifact is built from the full PubMed XML (~1650 files). A local mirror checks what changed against the server and downloads only the changed files. It is needed to compute NGD, which measures how often two concepts appear in the same paper. To compute it, ARAX needs the set of PubMed articles for every concept, and that is what curie_to_pmids provides. compute_ngd reads it for live calculations, and curie_ngd is built from it with precomputed values.

PubMed mirror (curie_pmids/mirror_pubmed.py):

- Lists baseline/ and updatefiles/ on ftp.ncbi.nlm.nih.gov/pubmed.
- Downloads a file only if it is missing locally, or its size or modification time differs from the server.
- Checks every download against NCBI's .md5 file and retries on checksum or network failures. Files are written to a .part file first and renamed only after the check passes.
- Sets each verified file's modification time to the server's, so the next run can skip it without hashing.
- Deletes local files that are no longer on the server, so the yearly baseline rollover does not leave old files to be parsed twice.
- Writes pubmed_manifest.json with the baseline release, file counts, how many were downloaded or removed, and start/end times.
- The mirror lives in pubmed_dir, outside the build directory, so every build reuses it.

Curie_to_PMIDs build (curie_pmids/build_ngd_database.py, curie_pmids/extraction_script.py):

Stage 1: PubMed to concept names (intermediate, not shipped)

- Parses every baseline and update file in parallel.
- From each article, collects MeSH descriptor names, chemical substance names, gene symbols and author keywords. MeSH qualifiers like "metabolism" are left out because they are modifiers, not concepts on their own.
- extraction_script.py cleans each name (normalizes dashes, quotes and Greek letters, removes duplicates, and drops prose, LaTeX, markup, etc.).
- Handles PubMed revision history: when an article appears in several files, only the newest version is kept, and PMIDs deleted by a later file (DeleteCitation) are dropped.
- Writes concept name → [PMIDs] to conceptname_to_pmids.sqlite.

Stage 2: concept names to CURIEs

- Resolves each name to a CURIE in the local Babel database, then moves to the preferred CURIE of that concept's clique, so PMIDs are stored under the ID that runtime lookups use.
- NAME_CURIE_OVERRIDES fixes known name collisions, like "MALE" colliding with the malE gene in E. coli.
- ~1.2M names resolve and ~7.1M don't (mostly free-text keywords).
- Collects PMIDs from Tier0 edges that carry publications, and attaches them to both endpoint CURIEs (~45M additional pairs).
- Merges both sources, removes duplicates, and writes each CURIE's PMIDs as a sorted little-endian int32 array.
- The intermediate conceptname_to_pmids.sqlite and the list of unresolved names are deleted with the work directory.

Output:

- curie_to_pmids(curie TEXT PK, pmids BLOB) ~1.27M CURIEs

### FDA_approved_drugs

- Consumes: DrugBank XML, Babel
- Contains: set of canonical CURIEs for approved drugs
- Consumed by: ARAX_expander
- For: the "FDA approved only" filter on drug nodes in Expand

Description of code: Expand applies the highest_FDA_approval_status constraint by keeping (or removing, if negated) answer nodes in this set. Its IDs must match the canonical IDs ARAX returns for nodes. This script (fda_drugs/generate_fda_pickle.py) produces the set of canonical CURIEs for FDA-approved drugs.

- Parses DrugBank for drugs whose groups include approved.
- Maps each DRUGBANK: ID to its Babel clique or cliques and keeps each clique's canonical ID.
- Pickles the resulting set.

NOTES: the filename has no build tag (fda_approved_drugs_v1.0.pickle every build). ARAX decides whether to download a database by the part of its filename after _v, so an ARAX instance that already has a fda_approved_drugs_v1.0.pickle will not download a new one with the same name. To push a changed FDA set to existing instances, it needs a new version in its filename.

### Curie_ngd

- Consumes: curie_to_pmids, nodes.jsonl, edges.jsonl
- Contains: precomputed NGD for each CURIE, as a ranked list of related CURIEs
- Consumed by: Overlay/compute_ngd.py, Path_Finder/utility.py, ARAX_connect.py (Pathfinder, xCRG)
- For: fast NGD lookups, and ranking which nodes Pathfinder explores

Description of code: computing NGD at query time is too slow. This (curie_ngd/build_curie_ngd.py) precomputes NGD between every CURIE and its graph neighbors. compute_ngd looks values up here first and computes live values only when one isn't here. Pathfinder uses the ranking to decide which neighbors to explore first. For each CURIE that has PMIDs and appears in the graph, it stores its neighbors sorted closest-first. Each entry includes the NGD value and the shared PMIDs that ARAX reports as the NGD edge's supporting publications.

- Loads every PMID array from curie_to_pmids into memory.
- Streams the edges and records neighbors in both directions, keeping only CURIEs that have PMIDs.
- Worker processes are forked and share the loaded arrays. For each CURIE the worker:
  - intersects the CURIE's PMID array with each neighbor's array
  - computes NGD with Pathfinder's formula and normalizer
  - drops pairs whose NGD is missing or 0
  - keeps the 30 smallest shared PMIDs in ascending order
  - sorts the entries by NGD, breaking ties by CURIE
- Writes one row per CURIE. A CURIE that is in the graph but has no neighbor with shared PMIDs gets [].

Output:

- curie_ngd(curie TEXT PK, ngd TEXT, pmid_length INT) ~759K rows
- Each NGD entry looks like [["NEIGHBOR:ID", ngd_score, [pmid, …]], …]
- pmid_length is the CURIE's own PMID count.

### ExplainableDTD (not built here)

- Consumes: Tier0 graph
- Contains: drug-disease prediction scores and explanation paths
- Consumed by: ARAX_infer.py (Infer/scripts/ExplainableDTD_db.py, infer_utilities.py)
- For: "what drugs might treat disease X" predictions in Infer

Built and staged separately. Its config_dbs.json entry (explainable_dtd_db) is carried over unchanged.

### COHD (not built here)

- Consumes: COHD clinical data mapped to KG2.8.0 IDs
- Contains: concept co-occurrence counts and statistics from patient records
- Consumed by: Overlay/overlay_clinical_info.py (COHDIndex.py)

Its config_dbs.json entry (cohd_database) is carried over unchanged.

### md5_sums

One .md5 per artifact, in md5sum format (hash, two spaces, bare filename), so `md5sum -c` works from the directory holding the artifacts. They are written only after the tests pass, so a build that fails its tests has nothing to stage.

They are used to check that the correct files are on each server. On the SFTP server, if the md5s are missing, the image will fail to build and the pod will enter a crash loop. A typo, a missing hash or an out-of-date hash will cause failures.

## Other scripts

- sources/babel.py: maps the graph's babelVersion to a stitch-proj release and downloads the sqlite (fetch_babel)
- sources/webhost.py: finds the newest DrugBank on the webhost and downloads it (fetch_drugbank)
- arax/update_config_dbs.py: writes a copy of RTX's config_dbs.json with the 5 artifacts pointed at the new tier0-YYYYMMDD directory. Every other entry is copied unchanged, and the RTX checkout is never modified.
- arax/stage_databases.py: stages a build on one of the SSH servers (see [Staging](#staging))
- arax/stage_sftp.py: stages a build on the ITRB SFTP server (see [Staging](#staging))
- arax/open_config_pr.py: opens the RTX PR (see [The RTX PR](#the-rtx-pr))
- report/artifact_stats.py: counts rows, keys and sizes in each artifact
- report/build_report.py: writes build_report.md, comparing counts with the last build in builds/
- report/record_build.py: copies the build's report, counts, config_dbs.json, PubMed manifest, md5s, test results, benchmarks, logs and staging/PR results to builds/YYYYMMDD/, and adds a Release section to the report
- report/push_build_record.py: commits builds/YYYYMMDD/ (and nothing else) and pushes it. It stops if the checkout is not on master, or has commits that are not on origin, or if staging or the PR was a dry run.

## Tests

- test_autocomplete.py: schema, prefix and substring completion of known terms, every graph node searchable, no empty or case-duplicate terms
- test_overlay.py: schema, the Fisher exact test category-count query, category counts follow the Biolink hierarchy, neighbor counts, neighbor IDs are graph nodes, edge publications
- test_curie_to_pmids.py: schema, PMIDs are sorted unique int32 blobs in range, xCRG read path, Tier0 edge PMIDs are included, the name collision override, CURIEs are Babel-preferred, ARAX lookups of hub CURIEs
- test_curie_ngd.py: schema, entry format, pmid_length and NGD values agree with curie_to_pmids, no missing rows, Pathfinder and xCRG read paths, ARAX lookup of a known pair
- test_fda_approved_drugs.py: is a set of CURIEs, IDs are Babel-preferred, contains known approved drugs
- test_config_dbs.py: required keys, built entries point at this build's files and have md5s, everything else is carried over, no sqlite sidecar files

The tests are local only. They never call Plover, Retriever or any other service. The build runs them itself; to run them by hand against a build:

```
python -m pytest tests -p no:cacheprovider --ignore tests/test_config_dbs.py \
    --build-dir /data/arax_databases/20260928 \
    --graph-dir /data/translator_kg/20260928 \
    --babel /data/babel/babel-20260722.sqlite \
    --biolink-cache-dir /data/arax_databases/.cache/biolink
```

--babel must be the Babel sqlite the build used (the dry run prints it). Each test finds its artifact with a glob (e.g. curie_ngd_v*_tier0-*.sqlite), so --build-dir must hold exactly one of each.

## Servers

### arax-databases.rtx.ai

- user: rtxconfig (rtxconfig@arax-databases.rtx.ai)
- location: /home/rtxconfig/tier0-<date>/<file>
- what it is: the database server. ARAX_database_manager.py on every ARAX instance downloads databases from here with rsync, using the paths in config_dbs.json. The RTX PR's CI downloads from here too.
- access: contact OSU

### sftp.transltr.io

- user: team-expander-USERNAME (team-expander-USERNAME@sftp.transltr.io)
- location:
  - /transltr-063208468694-sftp/team-expander/databases/tier0-<date>/<file>
  - /transltr-063208468694-sftp/team-expander/md5_sums/tier0-<date>/<file>.md5
  - sftp starts in /transltr-063208468694-sftp/team-expander, so the paths used are databases/... and md5_sums/...
- what it is: the ITRB drop. ITRB builds the transltr.io (CI, test, production) images from it. Nothing happens on ITRB's side until the RTX PR is merged into master.
- SFTP only, with S3 behind it: no shell, no md5sum, no rename, and an interrupted upload starts over.
- access: contact ITRB

### arax.ncats.io

- user: your own (USERNAME@arax.ncats.io), reached through the arax SSH alias (see [SSH access](#ssh-access))
- location: /translator/data/orangeboard/databases/tier0-<date>/<file>
- what it is: the production ARAX host. ARAX runs in the rtx2 container as user rt, which mounts /translator/data at the same path.
- Must be connected to an allowed network. Off site needs the VPN, or a jump host.
- access: contact OSU

### cicd.rtx.ai

- user: ubuntu (ubuntu@cicd.rtx.ai)
- location: /mnt/data/orangeboard/databases/tier0-<date>/<file>
- what it is: the CI host. It keeps its own copy of the databases. /mnt/data/orangeboard/databases is owned by root, so creating the release directory needs sudo.
- access: contact OSU

## Staging

Staging means putting a build's tested artifacts on every server that needs them, in a directory named for the release (tier0-<date>), before anything points at them. Every server has to have the files before the RTX PR is merged: once config_dbs.json on master points at tier0-<date>, every ARAX instance and ITRB image goes looking for them.

### Staging with the pipeline

In config.yaml set staging.enabled: true and staging.upload: true. A normal `snakemake -c24` then stages after the tests pass, before the RTX PR is opened.

To stage a build that is already built and tested, without the rest:

```
snakemake -c4 stage
```

Or one server at a time:

```
snakemake -c1 /data/arax_databases/20260928/staged/arax_databases.json
snakemake -c1 /data/arax_databases/20260928/staged/cicd.json
snakemake -c1 /data/arax_databases/20260928/staged/arax_ncats.json
snakemake -c1 /data/arax_databases/20260928/staged/itrb_sftp.json
```

When asking for one server's file like this, staging.enabled can stay false, but staging.upload must be true or it is a dry run. Run with staging.upload: false first if you want to see what it would do.

### What happens on arax-databases, cicd and arax

stage_databases.py:

1. Checks the local artifacts against their md5 files.
2. If tier0-<date> already exists on the server: if it holds exactly these files with these md5s, the step succeeds without uploading ("already_staged": true). If it holds anything else, the step stops. An existing release directory is never overwritten.
3. Checks there is enough free space for the files plus 10 GB.
4. Uploads into a hidden .staging-tier0-<date> directory (rsync, which resumes an interrupted upload; scp if the server has no rsync).
5. Checks every file's md5 on the server, then renames .staging-tier0-<date> to tier0-<date>, so the release directory never holds partial files.

- On cicd, the directory is created and renamed with sudo, since its parent is owned by root.
- On arax, it also checks that user rt inside the rtx2 container can read every file.
- The release directory gets dir_mode from config.yaml (775 on arax-databases, 777 on cicd and arax). Files are 644.
- Nothing on any server is ever deleted.

### What happens on sftp.transltr.io

stage_sftp.py:

1. Lists databases/tier0-<date> and md5_sums/tier0-<date>.
2. If they already hold exactly these files at the right sizes, the step succeeds without uploading. If they hold some of them at the right sizes (an interrupted run), only the missing ones are uploaded. If they hold anything else, or a file of the wrong size, the step stops.
3. Uploads the artifacts to databases/tier0-<date>/ first, then the md5 files to md5_sums/tier0-<date>/, so a release whose md5 files are all there is complete.
4. Checks every file's size after the upload. The server cannot compute an md5, so size is the check.

Nothing is ever overwritten or deleted.

### Staging results

Every server writes staged/<server>.json:

- {"staged": true, "location": ..., "already_staged": false}: uploaded and verified this run
- {"staged": true, "location": ..., "already_staged": true}: already there, verified, nothing uploaded
- {"staged": false, "dry_run": true, "location": ...}: dry run, nothing uploaded

### The RTX PR

open_config_pr.py:

1. Stops if any autobuild PR is still open on RTX (one at a time), or if this build's branch already exists.
2. Stops unless every artifact is on arax-databases with a matching md5, since the PR's CI downloads from there.
3. Clones RTX master into a temp directory, creates autobuild/tier0-<date> from its head, writes code/config_dbs.json, commits only that file, pushes the new branch (never forced) and opens a PR into master.

It never merges, approves or deletes anything. A person reviews and merges it.

To redo a build's PR: close the old one and delete its branch, delete rtx_pr.json from the build directory, and run the build again:

```
gh pr close <PR number> -R RTXteam/RTX --delete-branch
rm /data/arax_databases/20260928/rtx_pr.json
snakemake -c24
```

## Rebuilding one artifact

This is for when you changed one artifact's build script (or something it reads) and want only that artifact rebuilt. How you do it depends on whether the build has already been staged.

Each build script and the artifact it makes:

| Script you changed | Artifact | Snakemake rule | Also rebuilt |
|---|---|---|---|
| autocomplete/extract_names_ids.py, autocomplete/create_load_db.py | autocomplete | autocomplete | |
| overlay/generate_sqlite.py | tier0-info-for-overlay | overlay | |
| curie_pmids/build_ngd_database.py, curie_pmids/extraction_script.py | curie_to_pmids | curie_to_pmids | curie_ngd (it is built from curie_to_pmids) |
| curie_ngd/build_curie_ngd.py | curie_ngd | curie_ngd | |
| fda_drugs/generate_fda_pickle.py | fda_approved_drugs | fda_drugs | |

### If the build has not been staged yet

Let Snakemake do it. Every rule lists its own script as an input, so once you save a change to the script, Snakemake knows that artifact is out of date. It rebuilds that artifact and everything that depends on it (tests, md5s, config_dbs.json, the report), and leaves the other artifacts alone.

Edit and save the script. Then do a dry run and check that only that artifact and the steps after it are listed:

```
snakemake -n
```

Then run it (in tmux for anything long):

```
snakemake -c24
```

To rebuild an artifact without changing anything (for example after replacing a bad input), force its rule:

```
snakemake -c24 --forcerun curie_ngd
```

### If the build has already been staged

Do not run Snakemake for that build again after changing the script. It would rebuild the v1.0 file under the same name, and then stop at staging, because the servers already have a v1.0 file with a different md5.

Even if you copied it over by hand, ARAX would never pick it up: ARAX only downloads a database when the version in its filename changes (the part after _v, e.g. 1.0_tier0-20260928). An ARAX instance that already has curie_ngd_v1.0_tier0-20260928.sqlite keeps using it.

So the rebuilt artifact gets the next version number (v1.1, then v1.2 if it changes again) and goes next to the old one:

1. Build it as v1.1, in its own folder ([Build the new version](#build-the-new-version))
2. Test it ([Test it](#test-it))
3. Write its md5 ([Write its md5](#write-its-md5))
4. Copy it to all 4 servers ([Staging a single artifact](#staging-a-single-artifact))
5. Open an RTX PR pointing config_dbs.json at it ([Point ARAX at the new file](#point-arax-at-the-new-file))

The old v1.0 file stays on every server. Running ARAX instances use it until they update to the new config_dbs.json.

The examples below rebuild curie_ngd for build 20260928 as v1.1, with the example paths from [Configure](#configure). Change the artifact, date, version and paths to match yours. All commands are run from the repo root with the venv on PATH.

### Build the new version

Make a folder for it inside the build directory:

```
mkdir /data/arax_databases/20260928/v1.1
```

Then run the commands for your artifact. These are the same commands the Snakemake rules run, with the version in the output name changed. The Babel file must be the one the build used (it is listed under Inputs in build_report.md, and the dry run prints it).

curie_ngd:

```
ln -s ../curie_to_pmids_v1.0_tier0-20260928.sqlite /data/arax_databases/20260928/v1.1/
python workflow/scripts/curie_ngd/build_curie_ngd.py \
    --curie-to-pmids /data/arax_databases/20260928/curie_to_pmids_v1.0_tier0-20260928.sqlite \
    --nodes /data/translator_kg/20260928/nodes.jsonl \
    --edges /data/translator_kg/20260928/edges.jsonl \
    --output /data/arax_databases/20260928/v1.1/curie_ngd_v1.1_tier0-20260928.sqlite \
    --workers 24
```

The link to curie_to_pmids is only there so the curie_ngd tests can check against it. It is not staged.

autocomplete:

```
python workflow/scripts/autocomplete/extract_names_ids.py \
    /data/translator_kg/20260928/nodes.jsonl \
    /data/arax_databases/20260928/v1.1/names.tsv
python workflow/scripts/autocomplete/create_load_db.py \
    -i /data/arax_databases/20260928/v1.1/names.tsv \
    -o /data/arax_databases/20260928/v1.1/autocomplete_v1.1_tier0-20260928.sqlite
rm /data/arax_databases/20260928/v1.1/names.tsv
```

tier0-info-for-overlay (--schema-version sets the v1.1 in the filename):

```
python workflow/scripts/overlay/generate_sqlite.py \
    --nodes /data/translator_kg/20260928/nodes.jsonl \
    --edges /data/translator_kg/20260928/edges.jsonl \
    --graph-metadata /data/translator_kg/20260928/graph-metadata.json \
    --output-dir /data/arax_databases/20260928/v1.1 \
    --tier0-build-date 20260928 --schema-version 1.1 \
    --biolink-cache-dir /data/arax_databases/.cache/biolink
```

curie_to_pmids (and then curie_ngd from the new one, since curie_ngd is built from it):

```
mkdir /data/arax_databases/20260928/v1.1/work
python workflow/scripts/curie_pmids/build_ngd_database.py --full --skip-download \
    --pubmed-dir /data/pubmed \
    --babel-db /data/babel/babel-20260722.sqlite \
    --tier0-edges /data/translator_kg/20260928/edges.jsonl \
    --output-dir /data/arax_databases/20260928/v1.1/work
mv /data/arax_databases/20260928/v1.1/work/curie_to_pmids.sqlite \
    /data/arax_databases/20260928/v1.1/curie_to_pmids_v1.1_tier0-20260928.sqlite
rm -r /data/arax_databases/20260928/v1.1/work
python workflow/scripts/curie_ngd/build_curie_ngd.py \
    --curie-to-pmids /data/arax_databases/20260928/v1.1/curie_to_pmids_v1.1_tier0-20260928.sqlite \
    --nodes /data/translator_kg/20260928/nodes.jsonl \
    --edges /data/translator_kg/20260928/edges.jsonl \
    --output /data/arax_databases/20260928/v1.1/curie_ngd_v1.1_tier0-20260928.sqlite \
    --workers 24
```

This uses the PubMed mirror as it is now. Both new files get staged.

fda_approved_drugs:

```
python workflow/scripts/fda_drugs/generate_fda_pickle.py \
    --drugbank_xml /data/drugbank/drugbank-2023-01-04.xml \
    --babel_db /data/babel/babel-20260722.sqlite \
    --output_pickle /data/arax_databases/20260928/v1.1/fda_approved_drugs_v1.1.pickle
```

### Test it

Run the same tests the pipeline runs, against the v1.1 folder. Only run the test file for the artifact you rebuilt (curie_ngd here):

```
python -m pytest tests/test_curie_ngd.py -p no:cacheprovider \
    --build-dir /data/arax_databases/20260928/v1.1 \
    --graph-dir /data/translator_kg/20260928 \
    --babel /data/babel/babel-20260722.sqlite \
    --biolink-cache-dir /data/arax_databases/.cache/biolink
```

The test files are test_autocomplete.py, test_overlay.py, test_curie_to_pmids.py, test_curie_ngd.py and test_fda_approved_drugs.py. Do not stage anything that fails.

### Write its md5

Same format as the pipeline's (hash, two spaces, bare filename):

```
cd /data/arax_databases/20260928/v1.1
md5sum curie_ngd_v1.1_tier0-20260928.sqlite > curie_ngd_v1.1_tier0-20260928.sqlite.md5
cat curie_ngd_v1.1_tier0-20260928.sqlite.md5
```

## Staging a single artifact

The new file goes into the build's existing tier0-20260928 directory on each server, next to the old files. Copying it straight in is safe: nothing reads the new filename until config_dbs.json points at it. Never copy over a file that is already there.

Do arax-databases first, since arax.ncats.io copies from it. Example file: curie_ngd_v1.1_tier0-20260928.sqlite.

### On arax-databases.rtx.ai

Copy it up:

```
scp /data/arax_databases/20260928/v1.1/curie_ngd_v1.1_tier0-20260928.sqlite rtxconfig@arax-databases.rtx.ai:/home/rtxconfig/tier0-20260928/
```

Check it landed and the md5 matches the one in your .md5 file:

```
ssh rtxconfig@arax-databases.rtx.ai
cd /home/rtxconfig/tier0-20260928
ls -l
md5sum curie_ngd_v1.1_tier0-20260928.sqlite
exit
```

### On cicd.rtx.ai

The tier0-20260928 directory there is world-writable, so no sudo is needed to add a file:

```
scp /data/arax_databases/20260928/v1.1/curie_ngd_v1.1_tier0-20260928.sqlite ubuntu@cicd.rtx.ai:/mnt/data/orangeboard/databases/tier0-20260928/
```

Check it:

```
ssh ubuntu@cicd.rtx.ai
cd /mnt/data/orangeboard/databases/tier0-20260928
ls -l
md5sum curie_ngd_v1.1_tier0-20260928.sqlite
exit
```

### On arax.ncats.io

Copy it from arax-databases as user rt inside the rtx2 container, so the file is owned by the user ARAX runs as:

```
ssh arax
sudo docker exec -it rtx2 bash
su - rt
cd /translator/data/orangeboard/databases/tier0-20260928
scp rtxconfig@arax-databases.rtx.ai:/home/rtxconfig/tier0-20260928/curie_ngd_v1.1_tier0-20260928.sqlite .
ls -l
md5sum curie_ngd_v1.1_tier0-20260928.sqlite
exit
exit
exit
```

### On sftp.transltr.io

The artifact goes in databases/tier0-20260928 and its .md5 in md5_sums/tier0-20260928. Upload the artifact first and the .md5 last. There is no md5sum on this server, so check that the sizes in `ls -l` match your local files (`ls -l /data/arax_databases/20260928/v1.1`).

```
sftp team-expander-USERNAME@sftp.transltr.io
lcd /data/arax_databases/20260928/v1.1
cd databases/tier0-20260928
put curie_ngd_v1.1_tier0-20260928.sqlite
ls -l
cd ../../md5_sums/tier0-20260928
put curie_ngd_v1.1_tier0-20260928.sqlite.md5
ls -l
bye
```

### Point ARAX at the new file

Once the file is on all 4 servers, change config_dbs.json in RTX to use it, on a new branch and through a PR. In your RTX checkout:

```
cd RTX
git checkout master
git pull
git checkout -b curie_ngd-v1.1-tier0-20260928
```

In code/config_dbs.json, change only that artifact's line under database_downloads. For curie_ngd:

```
"curie_ngd": "/translator/data/orangeboard/databases/tier0-20260928/curie_ngd_v1.1_tier0-20260928.sqlite",
```

For tier0-info-for-overlay, change both tier0_sqlite and kg2c_sqlite. Then:

```
git diff
git commit -am "Use curie_ngd_v1.1_tier0-20260928.sqlite"
git push -u origin curie_ngd-v1.1-tier0-20260928
gh pr create --base master --fill
```

Someone reviews and merges it. ARAX instances download the new file the next time their database manager runs, and ITRB picks it up from the SFTP server. The branch name does not start with autobuild/, so the open PR does not block the pipeline's own PR.

## Troubleshooting

`No module named 'stitch'` (or biolink_helper_pkg): .venv/bin is not first on PATH. Run `export PATH=$PWD/.venv/bin:$PATH` from the repo root and rerun.

`IncompleteFilesException`: a previous run was interrupted. Rerun with `--rerun-incomplete`.

`No complete graph directory ... under graph_root`: the graph folder is missing one of nodes.jsonl, edges.jsonl, graph-metadata.json, or is not named YYYYMMDD. See [Tier0 graph](#tier0-graph).

`No ... release was built from Babel <version>`: stitch-proj has no Babel sqlite for the graph's Babel version yet. Ask for one to be built, or place the sqlite in babel_dir by hand, named babel-YYYYMMDD.sqlite.

A test failed: the log is logs/test_artifacts.log, and test_results/artifacts.xml has every result. Nothing was staged. Fix the cause and rerun; only the affected steps run again.

`Permission denied (publickey)` or a password prompt in a staging log: your SSH key does not work for that host. Run the checks in [SSH access](#ssh-access).

Staging stops with `already exists with different or missing files`: that server already has a tier0-<date> directory with other files in it. The pipeline never overwrites a release. If it is left over from a bad attempt and nothing uses it yet, remove it by hand on that server and rerun. If the release is live, see [Rebuilding one artifact](#rebuilding-one-artifact).

The RTX PR step stops with `An autobuild PR is still open` or `Branch ... already exists`: one autobuild PR at a time. Merge or close the open one (see [The RTX PR](#the-rtx-pr)) and rerun.

The record push stops with `has N commit(s) not on origin/master`: push or drop your own commits first, then rerun.
