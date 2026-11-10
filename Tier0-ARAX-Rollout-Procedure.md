# Tier0 ARAX Rollout Procedure

How to roll ARAX onto a new Tier0 (translator_kg) graph release, from downloading the graph to every production endpoint running on it.

Most of the database work is done by the **autobuild pipeline** ([Translator-CATRAX/arax-build-automation](https://github.com/Translator-CATRAX/arax-build-automation)). It builds, tests and stages the databases and opens the RTX PR. Everything around it (ExplainableDTD, code changes in RTX, testing, merging, production rollout and cleanup) is still done by hand, and is described here.

## Table of contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Phase 1: Kick off](#phase-1-kick-off)
- [Phase 2: Build, test and stage with autobuild](#phase-2-build-test-and-stage-with-autobuild)
- [Phase 3: ExplainableDTD (PSU)](#phase-3-explainabledtd-psu)
- [Phase 4: Integration on the autobuild branch](#phase-4-integration-on-the-autobuild-branch)
- [Phase 5: Test on dev and CI](#phase-5-test-on-dev-and-ci)
- [Phase 6: Merge to master](#phase-6-merge-to-master)
- [Phase 7: Production rollout](#phase-7-production-rollout)
- [Phase 8: Final cleanup](#phase-8-final-cleanup)
- [Rollback procedure](#rollback-procedure)
- [Reference: the artifacts](#reference-the-artifacts)
- [Reference: the servers](#reference-the-servers)
- [Reference: autobuild configuration](#reference-autobuild-configuration)
- [Reference: autobuild inputs](#reference-autobuild-inputs)
- [Reference: autobuild steps](#reference-autobuild-steps)
- [Reference: autobuild outputs](#reference-autobuild-outputs)
- [Reference: autobuild tests](#reference-autobuild-tests)
- [Reference: staging, the RTX PR and the build record](#reference-staging-the-rtx-pr-and-the-build-record)
- [Common operations](#common-operations)
- [Troubleshooting](#troubleshooting)
- [Developing autobuild](#developing-autobuild)
- [Copy-pasteable issue checklist](#copy-pasteable-issue-checklist)

---

## Overview

### Who does what

| Phase | What | Done by |
|---|---|---|
| 1 | Open the rollout issue, get the graph, coordinate PSU | by hand (OSU, typically Frankie) |
| 2 | Build, test and stage autocomplete, tier0-info-for-overlay, curie_to_pmids, curie_ngd, fda_approved_drugs; write `config_dbs.json`; open the RTX PR | **autobuild** |
| 3 | Build and stage ExplainableDTD | by hand (PSU, typically Chunyu; OSU copies it to the other servers) |
| 4 | Other RTX changes: OpenAPI versions, `generate-db-symlinks.sh`, `ARAX_database_manager.py` | by hand, on the autobuild branch |
| 5 | CI, development machine and `arax.ncats.io/test` testing | by hand |
| 6 | Tag, sign-off, merge | by hand |
| 7 | Endpoint rollout, ITRB check and promotion (ITRB's procedure), progressive production rollout | by hand |
| 8 | Changelog, old releases, retro | by hand |

### Phase gates

Don't start a phase until the previous one's gate is met. Record the evidence in the rollout issue; a stop condition halts the rollout until it's resolved or the rollout owner (the issue's assignee) explicitly accepts it there.

| Phase | Done when | Evidence to record | Stop if |
|---|---|---|---|
| 1 | graph chosen; PSU has the stamp; pre-flight checks pass | release version, `YYYYMMDD`, xDTD rebuild/refresh decision | a pre-flight check fails |
| 2 | `./autobuild --full` finished; build reviewed ([2e](#2e-review-the-build-report)) | PR link; build report link; inputs not in the report (Babel file, DrugBank version, RTX template commit); curie_to_pmids error counts; FDA set comparison | a test failed; parse or resolve errors unexplained; a count dropped without explanation |
| 3 | xDTD on all four servers, checksums match PSU's | PSU's md5; md5 on each server | any checksum mismatch |
| 4 | every [4a](#4a-database-paths)–[4c](#4c-generate-db-symlinkssh) item done and pushed | commit SHA of the branch head | FDA set changed but no new filename |
| 5 | CI, dev machine and `/test` all pass **on one commit** | that commit SHA; CI run link; test summaries | any failure on the recorded commit |
| 6 | merged, with the tested commit (or a retest after any later change) | merge method; merge/squash commit SHA; sign-offs | sign-off missing; branch head isn't the tested commit |
| 7 | every endpoint in the inventory on the new commit; CI/ITRB verified per its owner | per-endpoint previous and new commit; ITRB image/commit | an endpoint is occupied; a smoke test fails (go to [Rollback](#rollback-procedure)) |
| 8 | reference checks done before any deletion | what was deleted, where, when | anything still references a file |

### The whole flow

```
 Tier0 graph (fetched) ──▶ autobuild ───────────────────────────────────────────────┐
                           ├ fetch Babel, DrugBank, PubMed                           │
                           ├ build the 5 artifacts                                   │
                           ├ test them                         (stops on failure)    │
                           ├ md5s, config_dbs.json, build report                     │
                           ├ stage on arax-databases, cicd, arax.ncats.io, ITRB SFTP │
                           ├ open RTX PR  autobuild/tier0-YYYYMMDD → master          │
                           └ commit the build record to arax-build-automation        │
                                                                                     ▼
 PSU: ExplainableDTD ──▶ arax-databases ──▶ (OSU) other 3 servers         RTX PR (open)
                                                                                     │
 Phase 4: version bumps, symlink script, xDTD path ──▶ pushed to the PR branch ──────┤
                                                                                     ▼
 Phase 5: CI, dev machine, arax.ncats.io/test ──▶ Phase 6: tag, sign-off, merge to master
                                                                                     │
                                                                                     ▼
 Phase 7: arax.ncats.io endpoints, ITRB CI check + promotion, progressive rollout
                                                                                     │
                                                                                     ▼
 Phase 8: changelog, retire old releases after a week, close the issue
```

### Naming

The graph's build date, `YYYYMMDD`, names everything in a rollout:

| Thing | Name |
|---|---|
| graph directory | `{graph_root}/YYYYMMDD/` |
| artifacts | e.g. `curie_ngd_v1.0_tier0-YYYYMMDD.sqlite` |
| release directory on every server | `tier0-YYYYMMDD/` |
| RTX branch and PR | `autobuild/tier0-YYYYMMDD`, "Update config_dbs.json for Tier0 build YYYYMMDD" |
| build record | `builds/YYYYMMDD/` in arax-build-automation |
| rollback tag | `tier0-<previous YYYYMMDD>` |

---

## Prerequisites

### Access

Ask for everything before your first rollout. Each one blocks a different step.

| Access | Needed for | Who to ask |
|---|---|---|
| `rtxconfig@arax-databases.rtx.ai` | autobuild staging and PR check; xDTD copies | Oregon State team |
| `ubuntu@cicd.rtx.ai`, with passwordless sudo | autobuild staging; CI | Oregon State team |
| `USERNAME@arax.ncats.io` (bastion and endpoint), with passwordless `sudo docker` | autobuild staging; endpoint rollout | ITRB, via `#devops-teamexpanderagent` |
| `team-expander-USERNAME@sftp.transltr.io` | autobuild staging; xDTD upload | ITRB, via `#devops-teamexpanderagent` |
| `ubuntu@kg2webhost.rtx.ai` | autobuild DrugBank download (only contacted when `drugbank_version` is unpinned or the pinned file isn't local) | Oregon State team |
| `ubuntu@arax.rtx.ai` | listed in the earlier procedure; not used by autobuild | Oregon State team |
| write access to `RTXteam/RTX` | the PR, Phase 4 changes, merging | RTX team |
| push access to `Translator-CATRAX/arax-build-automation` | autobuild's build record | repo owners |

`ngdbuild2.rtx.ai` was where curie_to_pmids used to be built. autobuild builds everything on its own machine, so it isn't needed any more.

### Slack

- **ARAXTeam:** subscribe to `#deployment`. Post there before every endpoint rollout and before deleting anything on a server.
- **NCATSTranslator:** subscribe to `#devops-teamexpanderagent` (ITRB).

### SSH setup

autobuild runs unattended, so **every host must accept your key without a password prompt** (it connects with `BatchMode=yes`).

```bash
ssh-copy-id rtxconfig@arax-databases.rtx.ai
ssh-copy-id ubuntu@cicd.rtx.ai
ssh-copy-id ubuntu@kg2webhost.rtx.ai
```

For `sftp.transltr.io`, ITRB adds your public key to your team-expander account.

`arax.ncats.io` is reachable only through a bastion and enforces an IP allowlist. Off site, connect to the office VPN first. autobuild reaches it through an SSH alias named **`arax`**; you can keep a second alias with the full name for interactive use:

```
Host arax arax.ncats.io
    User USERNAME
    Hostname INTERNAL_IP
    IdentityFile ~/.ssh/id_rsa_long
    ProxyCommand ssh -i ~/.ssh/id_rsa_long -W %h:%p USERNAME@BASTION_IP
```

ITRB gives you access to both the bastion and the endpoint. On site or on the VPN, the `ProxyCommand` line may not be needed.

Check every one. Each must finish without asking for a password:

```bash
ssh -o BatchMode=yes rtxconfig@arax-databases.rtx.ai hostname
ssh -o BatchMode=yes ubuntu@cicd.rtx.ai 'sudo -n true && echo sudo ok'
ssh -o BatchMode=yes ubuntu@kg2webhost.rtx.ai 'head -c 300 /home/ubuntu/drugbank.xml'
ssh -o BatchMode=yes arax hostname
ssh -o BatchMode=yes arax 'sudo -n docker exec rtx2 sudo -u rt true && echo docker ok'
echo ls | sftp -o BatchMode=yes -b - team-expander-USERNAME@sftp.transltr.io
gh auth status
git ls-remote git@github.com:RTXteam/RTX HEAD
```

### The autobuild machine

| Resource | Needed | Why |
|---|---|---|
| OS | Linux | scripts use `md5sum`, `df --output`, `mv -T` |
| CPU | ~24 cores | curie_ngd uses the cores given to Snakemake (`-c`); curie_to_pmids's PubMed-parsing and name-resolving pools always use **every core on the machine minus one**, whatever `-c` says |
| RAM | 64 GB or more | curie_to_pmids peaked at 16.8 GB on a 24-core machine (more cores means more workers and more memory); curie_ngd's workers share about 13 GB |
| Disk | ~350 GB free | Babel sqlite ~185–220 GB, Tier0 graph ~32 GB unpacked, PubMed mirror ~70 GB, one build ~7 GB |

Software: Python 3.12, [uv](https://docs.astral.sh/uv/), git, [gh](https://cli.github.com/), ssh, rsync, scp, sftp, curl, zstd, tar, md5sum, and tmux (recommended).

Time: with the inputs already local, a full build takes about **45 minutes** (build 20260928: 44:29), plus staging about 7 GB to each of four servers. The **first** PubMed mirror and the **first** Babel download each take hours.

### Install autobuild

```bash
git clone git@github.com:Translator-CATRAX/arax-build-automation.git
git clone git@github.com:RTXteam/RTX.git
curl -LsSf https://astral.sh/uv/install.sh | sh     # if uv isn't installed
cd arax-build-automation
uv sync
gh auth login
```

`uv sync` creates `.venv/` with the versions pinned in `uv.lock`: snakemake, stitch-proj (local Babel lookups), biolink-helper-pkg, numpy, lxml, pyyaml and pytest.

`./autobuild` runs itself with that venv, so there's nothing to activate. To run `pytest`, `snakemake` or a script by hand, first `source .venv/bin/activate`; every rule runs its script with bare `python`, and a system `snakemake` fails with `No module named 'stitch'`.

### Configure autobuild

Shared settings are in `config/config.yaml`. Yours go in `config/config.local.yaml`, which git ignores and which overrides the shared file key by key (every key is described in [Reference: autobuild configuration](#reference-autobuild-configuration)):

```bash
cp config/config.local.example.yaml config/config.local.yaml
```

autobuild stops with a `Missing settings` error until these are set:

- `graph_root`, `babel_dir`, `pubmed_dir`, `drugbank_dir`, `output_root`: local directories
- `rtx_config_dbs`: your RTX checkout's `code/config_dbs.json`
- `staging.targets.itrb_sftp.host`: your own `team-expander-USERNAME@sftp.transltr.io` (only when staging)

Examples on this page use these paths; substitute your own:

| Example | What |
|---|---|
| `/data/translator_kg` | Tier0 graphs |
| `/data/babel` | Babel sqlites |
| `/data/pubmed` | PubMed mirror |
| `/data/drugbank` | DrugBank downloads |
| `/data/arax_databases` | autobuild output |
| `/path/to/RTX` | your RTX checkout that autobuild reads `config_dbs.json` from; keep it on `master` |
| `~/RTX-rollout` | a second RTX clone for hand-made RTX changes (Phase 4 onward) |

---

## Phase 1: Kick off

### 1a. Open the rollout issue

Create an issue in RTXteam/RTX titled like "Roll out ARAX Tier0-YYYYMMDD" and paste in the [issue checklist](#copy-pasteable-issue-checklist). Note the issue number; the PR autobuild opens and the changelog entry should reference it.

There is no `issue-XXXX` branch any more. autobuild creates `autobuild/tier0-YYYYMMDD` from master, and every other change for the rollout is pushed to that branch ([Phase 4](#phase-4-integration-on-the-autobuild-branch)).

### 1b. Choose the Tier0 graph

Releases are at <https://kgx-storage.ci.transltr.io/releases/translator_kg/> (no login). autobuild uses `translator_kg`, **not** `translator_kg_open`. Each release is a version folder (`1.0.2`, `1.0.3`, …) holding `translator_kg.tar.zst` (`nodes.jsonl`, `edges.jsonl` and `graph-metadata.json`, ~3.4 GB compressed, ~28 GB unpacked) and separate metadata files.

Find the newest release and its build date:

```bash
curl -s https://kgx-storage.ci.transltr.io/releases/translator_kg/latest-release.json
```

`release_version` (e.g. `1.0.2`) is the folder. The date part of `build_date` (`2026-09-28T23:50:46Z` → `20260928`) is the rollout's `YYYYMMDD`. autobuild downloads the graph to `{graph_root}/YYYYMMDD/` if it isn't there.

### 1c. Coordinate with PSU

Send PSU (typically @chunyuma for ExplainableDTD) the `YYYYMMDD` stamp and the graph's download location. Confirm at the start whether xDTD needs a full rebuild or only a refresh that maps old node and edge IDs to the new graph; that depends on how much this Tier0 version changed. See [Phase 3](#phase-3-explainabledtd-psu).

curie_ngd is now built by autobuild, so PSU (previously @mohsenht) no longer needs to build it.

### 1d. Pre-flight checks

- [ ] `graph_version` is set (1b, 2a), and there's room for the graph if it isn't local yet.
- [ ] Your RTX checkout is on an up-to-date `master`. **This matters:** autobuild makes the new `config_dbs.json` from it, and the PR replaces RTX's file with it, so a stale checkout would quietly revert other changes to that file.
  ```bash
  git -C /path/to/RTX switch master && git -C /path/to/RTX pull
  ```
- [ ] No autobuild PR is still open (autobuild allows one at a time):
  ```bash
  gh pr list --repo RTXteam/RTX --state open --json headRefName,url \
      --jq '.[] | select(.headRefName | startswith("autobuild/")) | .url'
  ```
- [ ] arax-build-automation is on `master` with nothing unpushed (the build record push requires it).
- [ ] All SSH checks pass ([Prerequisites](#ssh-setup)).
- [ ] **`arax-databases.rtx.ai` and `arax.ncats.io` each have at least 100 GB free.** autobuild only checks for the size of the files plus 10 GB, which doesn't leave room for xDTD and the next rollout. If you need to delete the oldest Tier0 (or KG2) release directory, **post in `#deployment` first**.
  ```bash
  ssh rtxconfig@arax-databases.rtx.ai 'df -h /home/rtxconfig'
  ssh arax 'df -h /translator/data/orangeboard/databases'
  ```
- [ ] The autobuild machine has enough free disk.

---

## Phase 2: Build, test and stage with autobuild

All commands run from the arax-build-automation checkout with `.venv/bin` on PATH.

### 2a. Set the graph version

In `config/config.local.yaml`, set `graph_version` to this rollout's `YYYYMMDD`. It's optional, and without it `--build` uses the latest release, but a rollout should pin it: otherwise rerunning a failed build after a new release would start a new build instead. Nothing in the config starts a release; only the flags you pass do.

### 2b. See the plan

```bash
./autobuild                     # the build plan; needs no server access
./autobuild --rehearse --full   # also checks every server and shows the PR diff; takes several minutes
```

Nothing changes. Check:

- the versions it picked, printed at the top:
  ```
  Tier0: using 20260928 from /data/translator_kg/20260928
  Babel: using 2026jul22 from /data/babel/babel-20260722.sqlite
  Biolink: 4.4.4
  ```
- whether it says it will download Babel or DrugBank
- that every server is reachable and has room

### 2c. Run it

In tmux, so it survives a dropped connection:

```bash
tmux new -s autobuild
set -o pipefail      # so the exit status is autobuild's, not tee's
./autobuild --full 2>&1 | tee ~/autobuild-run.log; echo "exit status: $?"
```

It asks before starting, and stops at the first failure. To release in steps instead, use `--build`, then `--stage`, `--pr` and `--record`. `-c24` limits the cores Snakemake schedules with (the default is all; curie_to_pmids ignores it; see [the autobuild machine](#the-autobuild-machine)). `Ctrl-b d` detaches; `tmux attach -t autobuild` reattaches.

Watch progress with `tail -f ~/autobuild-run.log`. Most steps write their own log in `/data/arax_databases/YYYYMMDD/logs/`, named after the file they write (`md5` has none; its output goes to Snakemake's).

If it fails, read the failed step's log, fix the cause and rerun the same command; finished steps are not redone. See [Troubleshooting](#troubleshooting).

### 2d. What it did

When it finishes:

- the five artifacts are built and tested in `/data/arax_databases/YYYYMMDD/`
- they're staged and verified in `tier0-YYYYMMDD/` on `arax-databases.rtx.ai`, `cicd.rtx.ai`, `arax.ncats.io` and `sftp.transltr.io` (with md5 files on SFTP)
- PR "Update config_dbs.json for Tier0 build YYYYMMDD" is open on RTXteam/RTX from `autobuild/tier0-YYYYMMDD`, with the diff and the build report in its description
- the build record is committed to `builds/YYYYMMDD/` in arax-build-automation

### 2e. Review the build report

Read `/data/arax_databases/YYYYMMDD/build_report.md` (also in the PR description and in `builds/YYYYMMDD/`). It lists the inputs, test totals, each artifact's size, time and peak memory, and each artifact's counts compared with the previous build. **Investigate any large unexplained drop** before going further. Record the PR link in the rollout issue.

The tests sample the artifacts; they don't prove the PubMed side is complete. curie_to_pmids's workers log a failure and carry on, so also check its log:

```bash
LOG=/data/arax_databases/YYYYMMDD/logs/curie_to_pmids_v1.0_tier0-YYYYMMDD.sqlite.log
grep -c "Parse failed" $LOG              # PubMed files that failed to parse; must be 0
grep "resolve error" $LOG | head         # name-resolution errors
grep "Recognized:" $LOG                  # recognized / unrecognized / errors totals
```

Any parse failure, or a resolve-error count or recognized-name count that's far off the previous build's (in `builds/<previous>/logs/`), stops the rollout until it's explained.

The report doesn't record every input. Add these to the rollout issue:

- the Babel file used (the `Babel: using ...` line from 2b, which includes the patch)
- the DrugBank version (`drugbank_version`, or the `DrugBank: using ...` line)
- the RTX template commit: `git -C /path/to/RTX rev-parse HEAD`

`pubmed_manifest.json` is a summary of the mirror when the build ran, not a per-file snapshot: the mirror is updated in place, so a later rebuild can't reproduce the same PubMed input.

**Check whether the FDA drug set changed.** Every build names it `fda_approved_drugs_v1.0.pickle`, and ARAX's database manager only re-downloads a database when the version after `_v` in its filename changes. So a changed set under the same name **never reaches** instances that already have a `v1.0`. Compare against the file master points at now:

```bash
OLD=$(python -c "import json;print(json.load(open('/path/to/RTX/code/config_dbs.json'))['database_downloads']['fda_approved_drugs'])")
echo "$OLD"      # e.g. /translator/data/orangeboard/databases/tier0-20260408/fda_approved_drugs_v1.0.pickle
scp rtxconfig@arax-databases.rtx.ai:/home/rtxconfig/$(basename $(dirname "$OLD"))/$(basename "$OLD") /tmp/fda_old.pickle
python -c "import pickle;a=pickle.load(open('/tmp/fda_old.pickle','rb'));b=pickle.load(open('/data/arax_databases/YYYYMMDD/fda_approved_drugs_v1.0.pickle','rb'));print('removed',len(a-b),'added',len(b-a))"
```

If either number is non-zero, the new set must go out under a new version before merging ([4a](#4a-database-paths)).

---

## Phase 3: ExplainableDTD (PSU)

ExplainableDTD (xDTD) is not built by autobuild. autobuild copies its `config_dbs.json` entry (`explainable_dtd_db`) unchanged from RTX master.

### 3a. Build (PSU)

- **Purpose:** precomputed predictions (treatment probability scores, with explainable mechanism-of-action paths) for all common drug–disease pairs, from the [KGML-xDTD](https://academic.oup.com/gigascience/article/doi/10.1093/gigascience/giad057/7246583) model trained on the Tier0 graph. Used by ARAX's `inferred` query mode: `ARAX_expander.py` → `ARAX_infer.py` (`Infer/scripts/ExplainableDTD_db.py`, `infer_utilities.py`).
- **Owner:** PSU (typically @chunyuma). Track it as a sub-issue of the rollout issue.
- **Build instructions:** <https://github.com/RTXteam/xDTD_training_pipeline>
- **PSU needs from OSU:** the `YYYYMMDD` stamp and the graph's download location (Phase 1c).
- **Rebuild or refresh:** sometimes xDTD can skip a rebuild and just be refreshed by mapping old node and edge IDs to the new graph's. Confirm with @chunyuma at the start.
- **Output:** `ExplainableDTD_v1.0_tier0-YYYYMMDD-all_with_paths.db`

### 3b. Stage on arax-databases (PSU)

PSU uploads it to `/home/rtxconfig/tier0-YYYYMMDD/` on `arax-databases.rtx.ai`, the directory autobuild created. Adding a file to it is safe: autobuild only checks its own files there, so an extra file doesn't affect a rerun.

### 3c. Copy to the other servers (OSU)

Copy it from arax-databases to `cicd.rtx.ai`, `arax.ncats.io` and `sftp.transltr.io`, with the same commands as for any single artifact ([Stage a single file by hand](#stage-a-single-file-by-hand)).

- **Staging order doesn't matter:** `autobuild --stage` only checks its own files, so xDTD can go up before or after it.
- **Ask PSU for the md5 of the file as they built it**, and record it in the rollout issue. Check every copy against that value (arax-databases, cicd, arax.ncats.io, and your local download), not against a hash of your own download: hashing a copy can't tell you the copy is corrupt.
- **Upload an md5 file for it too**, in `md5_sums/tier0-YYYYMMDD/`, uploaded after the database. Without a correct md5, ITRB's image build fails and the pod crash-loops.

```bash
# on your machine, after downloading the file from arax-databases:
md5sum ExplainableDTD_v1.0_tier0-YYYYMMDD-all_with_paths.db     # must equal PSU's md5
md5sum ExplainableDTD_v1.0_tier0-YYYYMMDD-all_with_paths.db > ExplainableDTD_v1.0_tier0-YYYYMMDD-all_with_paths.db.md5
```

Files already in the SFTP release directory, such as xDTD, don't affect `autobuild --stage`: it only checks the files it's uploading.

### 3d. Point config_dbs.json at it

Update `explainable_dtd_db` in `code/config_dbs.json` on the PR branch ([Phase 4a](#4a-database-paths)). If PSU changes `config_dbs.json` on master before autobuild runs, autobuild carries the new entry over and there's nothing to do.

### Artifacts carried over unchanged

- **COHD** (`COHDdatabase_v1.0_KG2.8.0.db`, key `cohd_database`): carried forward unchanged. The KG2.8.0 stamp is historical; it isn't regenerated for a Tier0 rollout.
- **fda_approved_drugs** used to be reused as-is. autobuild now rebuilds it in every build, from the DrugBank version pinned by `drugbank_version` (2023-01-04 so far) or, without a pin, the newest one, but it keeps the name `fda_approved_drugs_v1.0.pickle` (see [the artifact notes](#fda_approved_drugs)).

---

## Phase 4: Integration on the autobuild branch

autobuild's PR changes only `code/config_dbs.json`. Everything else goes on the same branch, so it's reviewed and merged together.

**Do this in a separate RTX clone, not the checkout `rtx_config_dbs` points at.** That checkout's `config_dbs.json` is an input to the build, so switching it to the autobuild branch or editing it would change what a later `--build` writes.

```bash
git clone git@github.com:RTXteam/RTX.git ~/RTX-rollout     # once
cd ~/RTX-rollout
git fetch origin
git switch autobuild/tier0-YYYYMMDD
# ...make the changes below...
git commit -am "<what changed> for Tier0 YYYYMMDD (#<issue>)"
git push origin autobuild/tier0-YYYYMMDD
```

### 4a. Database paths

- [ ] Check autobuild's diff in the PR. It sets `info_for_overlay_sqlite`, `autocomplete`, `curie_to_pmids`, `curie_ngd` and `fda_approved_drugs` to `/translator/data/orangeboard/databases/tier0-YYYYMMDD/...`.
- [ ] Set `explainable_dtd_db` to the new xDTD file (Phase 3), unless it was already updated on master.
- [ ] **If the FDA drug set changed** ([2e](#2e-review-the-build-report)): publish it under a version higher than the one master uses now (e.g. `fda_approved_drugs_v1.1.pickle`; check no file of that name exists in any release directory). Copy the built pickle to that name, write its md5, stage it on all four servers ([Stage a single file by hand](#stage-a-single-file-by-hand)), and point `fda_approved_drugs` at it on this branch. In `code/generate-db-symlinks.sh`, change the `fda_approved_drugs_v1.0.pickle` link line to the new name too. Don't merge until this is done.
- [ ] Update `code/ARAX/ARAXQuery/ARAX_database_manager.py` only if a key name changed. The paths themselves come from `config_dbs.json` through `RTXConfiguration`.
- [ ] Check nothing else in RTX hardcodes the previous Tier0 stamp:
  ```bash
  git grep -n "tier0-<previous YYYYMMDD>"
  ```

### 4b. API version numbers

If this rollout includes a TRAPI or ARAX version change, bump the `version` field (around line 12) in each of these. For a pure database refresh with no API change you can skip it, but **confirm with @edeutsch**. (List checked against RTX master at `7c624946d`; the old `python-flask-server/KG2/...` file no longer exists. Run `git ls-files '*openapi.yaml'` to catch any change.)

- [ ] `code/UI/OpenAPI/python-flask-server/openapi_server/openapi/openapi.yaml`
- [ ] `code/UI/OpenAPI/specifications/export/ARAX/1.5.0/openapi.yaml`
- [ ] `code/UI/OpenAPI/specifications/export/KG2/1.5.0/openapi.yaml`

Also set `biolink-version` in each to the graph's Biolink version if it changed. That's the "Biolink model" line in the build report's Inputs, and the version autobuild built tier0-info-for-overlay with.

### 4c. `generate-db-symlinks.sh`

- [ ] In `code/generate-db-symlinks.sh`, set `LATEST_TIER0_VER` to the new release:
  ```bash
  LATEST_TIER0_VER="tier0-YYYYMMDD"
  ```

---

## Phase 5: Test on dev and CI

### 5a. CI

- [ ] The PR's CI downloads the databases from `arax-databases.rtx.ai` (autobuild checked they're there with matching md5s). Trigger a [Test Build](https://github.com/RTXteam/RTX/actions/workflows/pytest.yml) of `autobuild/tier0-YYYYMMDD` on [cicd.rtx.ai](http://cicd.rtx.ai/) and confirm the full pytest suite under `code/ARAX/test/` passes (all non-skipped tests).
- If the CI host can't find the databases: autobuild already placed them in `/mnt/data/orangeboard/databases/tier0-YYYYMMDD/` on cicd. The earlier manual procedure ran the database manager there, which you can still do:
  ```bash
  ssh ubuntu@cicd.rtx.ai
  cd RTX && git fetch origin && git switch autobuild/tier0-YYYYMMDD && git pull
  python3.12 -m venv ~/venv-rtx && ~/venv-rtx/bin/pip install -r requirements.txt   # once; CI uses Python 3.12
  ~/venv-rtx/bin/python code/ARAX/ARAXQuery/ARAX_database_manager.py --mnt --skip-if-exists
  ```
  Don't add `--remove_unused` by hand. Note that the Test Build workflow itself runs the database manager **with** `--remove_unused` on cicd's shared `/mnt/data/orangeboard/databases`. As of RTX `7c624946d` that deletes only files inside `KG2*` directories that the branch's `config_dbs.json` doesn't name, so `tier0-*` releases are safe, but a CI run can still remove KG2-era files another branch or preview container uses.

### 5b. Catch up with master

- [ ] Merge `master` into `autobuild/tier0-YYYYMMDD` if master has moved since autobuild branched, and resolve any conflict in `config_dbs.json` by keeping the new Tier0 paths and master's other changes. Do this **before** 5a, 5c and 5d, so all three test the same commit.

### 5c. Test on a development machine

RTX needs its own environment; autobuild's `.venv` doesn't have RTX's dependencies. These steps mirror what the Test Build workflow (`.github/workflows/pytest.yml`) does; if your machine can't do one of them, test on a [preview container](https://github.com/RTXteam/RTX/blob/master/deploy/preview/README.md) or `/test` instead.

- [ ] Make a **clean** checkout of `autobuild/tier0-YYYYMMDD` (or [delete the TRAPI query cache](https://github.com/RTXteam/RTX/wiki/ARAX-Maintenance-SOP#troubleshooting-tips) in an existing one).
- [ ] Create a Python 3.12 venv and install RTX's dependencies from the repo root: `pip install -r requirements.txt`.
- [ ] Get `code/config_secrets.json` (CI copies it from `araxconfig@araxconfig.rtx.ai:config_secrets.json`; ask the Oregon State team for access).
- [ ] Download the databases: `python code/ARAX/ARAXQuery/ARAX_database_manager.py` from the repo root. This fetches every `config_dbs.json` entry, including the carried-over COHD and xDTD files, from `arax-databases.rtx.ai`; it needs SSH access there.
- [ ] Build the KP cache: `cd code/ARAX/ARAXQuery/Expand && python kp_info_cacher.py`.
- [ ] Record the commit you're testing (`git rev-parse HEAD`) in the rollout issue.
- [ ] Run the suite: `cd code/ARAX/test && pytest -v`.
- [ ] Run the ARAX server: `cd code/UI/OpenAPI/python-flask-server && python -m openapi_server` (port 5000 by default). Run all four example queries (UI → Examples) and inspect both the results **and** the TRAPI message logs.
- [ ] Smoke-check that the new Tier0 is what's loaded: pick a Tier0-only CURIE (or a version-stamped node) that the previous release didn't have, confirm it resolves, and look at the Synonyms tab in the UI.

### 5d. Test on arax.ncats.io/test

- [ ] Install the branch on [arax.ncats.io/test](http://arax.ncats.io/test) (same steps as [7a](#7a-roll-out-to-the-araxncatsio-endpoints), with `ENDPOINT=test` and the branch instead of master) and re-test there.
- [ ] Check STDERR in `/tmp/RTX_OpenAPI_test.elog` and confirm the Background Tasker is working.

---

## Phase 6: Merge to master

- [ ] **Tag the head of `master` with the previous Tier0 stamp before merging.** This is the rollback anchor:
  ```bash
  git switch master && git pull
  git tag tier0-<previous YYYYMMDD>
  git push origin tier0-<previous YYYYMMDD>
  ```
- [ ] Compare the branch head with the commit recorded in Phase 5 (`git rev-parse origin/autobuild/tier0-YYYYMMDD`). If master has moved, merge it into the branch again; **any change to the head after Phase 5, including that merge, means rerunning CI (5a) and at least the dev-machine pytest (5c)** on the new head, and recording the new SHA.
- [ ] Get agreement that it's ready. Ideally:
  - [ ] @hodgesf
  - [ ] @bazarkua
  - [ ] @saramsey
  - [ ] @dkoslicki
  - [ ] @edeutsch
- [ ] Merge the PR into `master`, with the rollout issue number in the merge message. Record the merge method (merge commit, squash or rebase) and the resulting commit SHA(s) on master in the rollout issue; [rollback](#rollback-procedure) depends on both.

autobuild never merges its own PR. Once it's merged, every ARAX instance's database manager downloads the new filenames from `arax-databases.rtx.ai` the next time it runs. **How and when ITRB deploys the new master is not something autobuild or RTX's workflows control or verify** (RTX's Test Build only tests a container on cicd); see [7b](#7b-check-the-itrb-ci-deployment).

---

## Phase 7: Production rollout

### 7a. Roll out to the arax.ncats.io endpoints

**First, write the endpoint inventory** into the rollout issue: one row per endpoint you'll touch, taken from the current [ARAX Maintenance SOP](https://github.com/RTXteam/RTX/wiki/ARAX-Maintenance-SOP) and checked on the host. Don't assume a container: the SOP uses `rtx2` for the shared devareas, and autobuild's read check also uses `rtx2`.

| Endpoint | Container | Checkout | Service | Port | Python | Commit before | Commit after |
|---|---|---|---|---|---|---|---|
| e.g. `test` | from the SOP | `/mnt/data/orangeboard/test/RTX` | `RTX_OpenAPI_test` | | `python3` the service runs | `git rev-parse HEAD` | |

**Post in `#deployment` (ARAXTeam) before each endpoint**, with the Tier0 stamp. For each endpoint (`ENDPOINT` and `CONTAINER` from the inventory):

- [ ] Off site, connect to the office VPN.
- [ ] `ssh arax.ncats.io`
- [ ] Enter the container: `sudo docker exec -it CONTAINER bash`
- [ ] Become `rt`: `su - rt`
- [ ] `cd /mnt/data/orangeboard/ENDPOINT/RTX`
- [ ] **Check the endpoint isn't occupied:** `git status` and `git branch --show-current`. It must be on `master` with no tracked changes. If it's on another branch or has local edits, someone is using it: find out who (the SOP and `#deployment`), and don't touch it until they've released it. Never stash or discard someone else's work.
- [ ] Record `git rev-parse HEAD` as "Commit before" in the inventory.
- [ ] `git pull --ff-only origin master`, then record the new `git rev-parse HEAD` as "Commit after".
- [ ] If `requirements.txt` (at the repo root) changed: `pip3 install -r requirements.txt`, with the Python the service uses (inventory).
- [ ] Back to root: `exit`
- [ ] `service RTX_OpenAPI_ENDPOINT stop`
- [ ] Check that this endpoint's flask service and Background Tasker are gone from the process table (`ps aux | grep ENDPOINT`)
- [ ] In `RTX/code/ARAX/ARAXQuery/Expand`, [delete the TRAPI query cache](https://github.com/RTXteam/RTX/wiki/ARAX-Maintenance-SOP#troubleshooting-tips)
- [ ] `service RTX_OpenAPI_ENDPOINT start`
- [ ] `tail -f /tmp/RTX_OpenAPI_ENDPOINT.elog`: watch start-up for errors and confirm the Background Tasker starts
- [ ] In the browser, run the four example queries against this endpoint; check results and TRAPI logs
- [ ] Confirm the new Tier0 is loaded with a Tier0-only test query

After every dev endpoint is up, run the tests on each, inside its container:

```bash
cd /mnt/data/orangeboard/ENDPOINT/RTX/code/ARAX/test && pytest -v
```

### 7b. Check the ITRB CI deployment

autobuild and RTX don't deploy to ITRB. Before Phase 6, get the **current** deployment procedure from whoever owns it (ask in `#devops-teamexpanderagent`): what triggers a CI deployment after a master merge, how to see which commit or image is deployed, and how CI is promoted to test and production. Record it in the rollout issue. Then:

- [ ] Confirm the deployed commit or image on `arax.ci.transltr.io` is the merge commit from Phase 6, by the owner's method. A Tier0-only query that succeeds is supporting evidence, not proof.
- [ ] Run live queries against CI: the four example queries and a Tier0-only query, sent to `https://arax.ci.transltr.io` (record the queries and results).
- [ ] RTX's `pytest` always runs local ARAX code; it has no option to target a remote endpoint. To test what CI runs, either run pytest **inside the deployed image** (if ITRB gives you access to it), or run it on a local checkout of the exact deployed commit with the same `config_dbs.json`. Record which you did.

### 7c. Progressive production rollout

- [ ] Roll the new `master` to the `arax.ncats.io` production endpoints **one at a time**, smoke-testing after each (same steps as 7a, including the occupancy check and the inventory).
- [ ] **Leave at least one legacy endpoint on the previous Tier0** until the new build has been stable in production for at least a week.
- [ ] Promote ITRB test and production per the owner's procedure (7b), verifying the deployed commit or image after each.

---

## Phase 8: Final cleanup

- [ ] Add an entry for this rollout to the current RTX changelog issue: date, Tier0 stamp, what changed (the build report's Counts section is a good summary).
- [ ] Delete `autobuild/tier0-YYYYMMDD` in RTX once it's merged **and** no CI run, preview container or endpoint is still building from it (check the Actions tab and `docker ps` on cicd).
- [ ] After a week of stability, move the last legacy endpoint to the new build.
- [ ] After a week of stability, delete the previous Tier0 release directory on `arax-databases.rtx.ai` and `arax.ncats.io` to reclaim disk, **only after a reference check**:
  - no endpoint in the inventory, preview container or CI branch still runs a commit whose `config_dbs.json` names that directory (check each one's `code/config_dbs.json`)
  - master's `config_dbs.json` doesn't name it. Carried-over entries can point at an old directory: today `fda_approved_drugs` points at `tier0-20260408` and `explainable_dtd_db` at `tier0-20260621`
  - the rollback plan no longer needs it (the previous-release tag is no longer a rollback target)

  **Post in `#deployment` first.** Do **not** delete the previous build's copy on the ITRB SFTP until the rollout after the next one.
- [ ] Optionally remove old build directories, graphs and Babel files from the autobuild machine ([Free disk space](#free-disk-space)).
- [ ] Close the rollout issue with a short retro: what went well, what didn't, what to change next time.

---

## Rollback procedure

If the new build misbehaves after rollout:

1. **Confirm the previous release's files are still present**, before restarting anything: every file the previous `config_dbs.json` names must exist on `arax-databases.rtx.ai`, on `arax.ncats.io` and on the ITRB SFTP. Check with `git show tier0-<previous YYYYMMDD>:code/config_dbs.json` and `ls` on each server. If any is gone, restore it first; an endpoint restarted onto missing files won't come up.
2. **Revert the affected endpoints** to the "Commit before" recorded for each one in the 7a inventory (normally the tag `tier0-<previous YYYYMMDD>`, created in Phase 6). On each, follow the Phase 7a steps (including the occupancy check) with `git checkout <commit before>` in place of the pull, then restart the service. The legacy endpoint kept in Phase 7c is untouched and can take traffic meanwhile.
3. **Restore the database paths on master**, through a PR. How depends on the merge method recorded in Phase 6:
   ```bash
   git switch master && git pull
   git switch -c rollback-tier0-YYYYMMDD
   # merge commit: check parent 1 is the old master, then revert the merge
   git log -1 --format='%P' <merge commit>    # first SHA should be tier0-<previous YYYYMMDD>
   git revert -m 1 <merge commit>
   # squash merge: one ordinary commit, no -m
   git revert <squash commit>
   # rebase merge: revert each rebased commit, newest first
   git revert --no-commit <oldest>^..<newest> && git commit
   git diff tier0-<previous YYYYMMDD> -- code/config_dbs.json   # should show only changes made after the rollout
   git push -u origin rollback-tier0-YYYYMMDD
   gh pr create --base master --fill
   ```
   Or hot-fix the deployed copy if it's urgent. For ITRB, follow the deployment owner's procedure (7b) and verify the deployed commit.
4. **Post in `#deployment`** and update the rollout issue with what failed and why.

---

## Reference: the artifacts

| Artifact | Built by | From | Used in ARAX by | `config_dbs.json` key |
|---|---|---|---|---|
| autocomplete | autobuild | Tier0 nodes | type-ahead in the UI | `autocomplete` |
| tier0-info-for-overlay | autobuild | Tier0 nodes, edges, metadata | edge publications, Fisher's exact test, Infer, Pathfinder | `info_for_overlay_sqlite` |
| curie_to_pmids | autobuild | PubMed, Babel, Tier0 edges | live NGD, xCRG | `curie_to_pmids` |
| curie_ngd | autobuild | curie_to_pmids, Tier0 | precomputed NGD, Pathfinder, xCRG | `curie_ngd` |
| fda_approved_drugs | autobuild | DrugBank, Babel | Expand's FDA-approved filter | `fda_approved_drugs` |
| ExplainableDTD | PSU | Tier0 | Infer (`inferred` mode) | `explainable_dtd_db` |
| COHD | not rebuilt | COHD, KG2.8.0 IDs | `Overlay/overlay_clinical_info.py` | `cohd_database` |

```
    Tier0 graph                Babel sqlite              PubMed mirror        DrugBank
    (fetched)                  (stitch-proj release)     (NCBI FTP)           (webhost)
         │                          │                         │                   │
         ├──────────────► autocomplete                        │                   │
         ├──────────────► tier0-info-for-overlay              │                   │
         ├──────────────► ExplainableDTD (PSU)                │                   │
         ├──────────────► curie_to_pmids ◄── Babel ◄──────────┘                   │
         │                     │                                                  │
         │                     ▼                                                  │
         └──────────────► curie_ngd                                               │
                              Babel ──────────► fda_approved_drugs ◄──────────────┘
```

Counts below are from build 20260928.

### autocomplete

- **Used by:** `code/autocomplete/rtxcomplete.py`, for type-ahead in the ARAX UI (prefix and substring queries).
- **Schema:** `terms(term VARCHAR(255) COLLATE NOCASE)`, index `idx_terms_term`; ~3.6M rows. No fragment-cache tables: rtxcomplete keeps its cache in its own `rtxcomplete_cache.sqlite`.
- **Build** (`workflow/scripts/autocomplete/`):
  1. `extract_names_ids.py` streams `nodes.jsonl` and writes `id<TAB>name` per node to a scratch TSV, collapsing whitespace and skipping lines that aren't valid JSON. It reads plain text, not `.gz`.
  2. `create_load_db.py` loads both the name and the CURIE of each node, keeping only the first spelling of a term when case is ignored ("ASPIRIN", "Aspirin", "aspirin" become one) and skipping empty names. An existing output is deleted and recreated.
  3. The index is built after loading and the TSV is deleted.

### tier0-info-for-overlay

- **Used by:** `ARAX_decorator` (edge publications), `Overlay/fisher_exact_test.py`, `Infer/scripts/infer_utilities.py`, `Path_Finder/utility.py` (node degree). `info_for_overlay_sqlite` points at it.
- **Schema:** a small extract, **not** an ingest of the graph; there are no `nodes` or `edges` tables. Anything needing full node or edge properties should query Tier0.
  - `edge_publications(arax_edge_key TEXT PRIMARY KEY, publications TEXT)`: ~8.2M rows; PMIDs joined with `ǂ` (U+01C2)
  - `neighbors(id TEXT PRIMARY KEY, neighbor_counts TEXT)`: ~1.77M rows; JSON `{expanded Biolink category: count}`
  - `category_counts(category TEXT PRIMARY KEY, count INTEGER)`: ~68 rows; nodes whose expanded categories include it
- **Build** (`workflow/scripts/overlay/generate_sqlite.py`):
  1. Loads every node's categories into memory and expands them to all Biolink ancestors, including mixins, with BiolinkHelper **at the graph's Biolink version** (from `graph-metadata.json`, not ARAX's pinned version).
  2. Streams the edges once. Edges with publications get an `edge_publications` row; every edge counts toward neighbors.
  3. Writes `{output-dir}/tier0-info-for-overlay_v{schema-version}_tier0-{date}.sqlite`. `--schema-version` defaults to `1.0`.
- **The edge key** is `subject--predicate--qualified_predicate--object_direction_qualifier--object_aspect_qualifier--object--primary_knowledge_source`, with missing qualifiers left empty. It copies `ARAXQuery/util.get_arax_edge_key()` in RTX. **If either changes, both must**, or lookups silently miss.

### curie_to_pmids

- **Used by:** `Overlay/compute_ngd.py` (live NGD), `ARAX_connect.py` (xCRG). curie_ngd is built from it.
- **Schema:** `curie_to_pmids(curie TEXT PRIMARY KEY, pmids BLOB)`; each blob is a sorted, unique, little-endian int32 array. ~1.27M CURIEs, ~400M (CURIE, PMID) pairs.
- **Build** (`workflow/scripts/curie_pmids/build_ngd_database.py --full --skip-download`; autobuild refreshes the PubMed mirror itself first):
  - **Stage 1, PubMed → concept names** (intermediate `conceptname_to_pmids.sqlite`, not shipped):
    1. Parses every baseline and update file in parallel.
    2. Collects MeSH descriptor names, chemical substance names, gene symbols and author keywords per article. MeSH qualifiers (e.g. "metabolism") are left out.
    3. `extraction_script.py` cleans names: normalizes dashes, quotes and Greek letters, removes duplicates, and drops prose, LaTeX and markup.
    4. Keeps only the newest version of an article that appears in several files, and drops PMIDs deleted by a later file (`DeleteCitation`).
  - **Stage 2, names → CURIEs:**
    1. Resolves each name in the local Babel sqlite, then to its clique's preferred CURIE.
    2. `NAME_CURIE_OVERRIDES` fixes known collisions (e.g. "MALE" vs. the E. coli gene malE).
    3. Adds the PMIDs of every Tier0 edge with publications to both endpoints (~45M extra pairs). Without `--tier0-edges` this is skipped with only a warning; autobuild always passes it.
    4. Merges, removes duplicates and writes the arrays.
  - ~1.2M names resolve; ~7.1M don't (mostly free-text keywords). The intermediate, `unrecognized_pubmed_concept_names.txt` and the work directory are deleted.
- The script's other modes (`--full` without `--skip-download` mirrors PubMed with `wget`; no flags runs only stage 2 against an existing `conceptname_to_pmids.sqlite`) aren't used by autobuild.

### curie_ngd

- **Used by:** `Overlay/compute_ngd.py` (`_lookup_precomputed_ngd`, tried before computing live), `Path_Finder/utility.py::get_curie_ngd_path` and `ARAX_connect.py` (Pathfinder ranks neighbors to explore; xCRG).
- **Schema:** `curie_ngd(curie TEXT PRIMARY KEY, ngd TEXT, pmid_length INTEGER)`; ~759K rows.
  - `ngd` is JSON `[["NEIGHBOR:CURIE", 0.12, [1280572, 1293713, ...]], ...]`, closest first
  - `pmid_length` is the CURIE's own PMID count
- This is the **v1.1 data format**; the `v1.0` in the filename is the build version. It reproduces Pathfinder's builder ([pathfinder `build_model/db_build`](https://github.com/Translator-CATRAX/pathfinder/blob/master/build_model/db_build/README.md)): same neighbors, formula, normalizer and filtering. The only difference is that PMID sets are held in shared memory instead of Redis.
- **Build** (`workflow/scripts/curie_ngd/build_curie_ngd.py`):
  1. Loads every PMID array from curie_to_pmids into memory.
  2. Streams the edges, recording neighbors in both directions, for CURIEs that have PMIDs.
  3. Forked workers, for each CURIE in the graph: intersect its PMIDs with each neighbor's; compute NGD with Pathfinder's formula (normalizer 3.5e7 articles × 20 MeSH terms); drop pairs with NGD missing or 0; keep the 30 smallest shared PMIDs (ARAX reports them as the NGD edge's supporting publications); sort by NGD, ties by CURIE.
  4. One row per CURIE. A CURIE with no neighbor sharing PMIDs gets `[]`.
- Options: `--num-pubmed-articles` (3.5e7), `--avg-mesh-terms-per-article` (20), `--max-shared-pmids` (30), `--workers`.

### fda_approved_drugs

- **Used by:** `ARAX_expander`, for the `highest_FDA_approval_status` constraint: answer nodes are kept (or, negated, removed) based on membership.
- **Format:** a pickled Python `set` of canonical CURIEs; ~4,800.
- **Build** (`workflow/scripts/fda_drugs/generate_fda_pickle.py`): collects drugs whose groups include `approved` as `DRUGBANK:` CURIEs, maps each to its Babel clique(s) and keeps each clique's canonical ID (to match the node IDs ARAX returns), and pickles the set.
- **Note:** the filename has no Tier0 tag, so every build writes `fda_approved_drugs_v1.0.pickle`. ARAX decides whether to download by filename, so an instance that already has that name won't fetch a new one. To push a changed set to existing instances, give it a new version ([Rebuild one artifact](#rebuild-one-artifact)).

### md5 files

One per artifact, in `md5sum` format (hash, two spaces, bare filename), so `md5sum -c` works from any directory holding the artifacts. autobuild writes them only after the tests pass. On the ITRB SFTP they're required: a missing, mistyped or out-of-date md5 makes ITRB's image build fail and the pod crash-loop.

---

## Reference: the servers

| Server | Login | Release location | Role |
|---|---|---|---|
| arax-databases.rtx.ai | `rtxconfig` | `/home/rtxconfig/tier0-YYYYMMDD/` | the database server. Every ARAX instance's `ARAX_database_manager.py` downloads from here, using the paths in `config_dbs.json`; so does the PR's CI. Release directory mode `775`. |
| cicd.rtx.ai | `ubuntu` | `/mnt/data/orangeboard/databases/tier0-YYYYMMDD/` | the CI host, with its own copy. The parent is root-owned, so autobuild creates and renames the release directory with `sudo -n`. Mode `777`. |
| arax.ncats.io | your own, via alias `arax` | `/translator/data/orangeboard/databases/tier0-YYYYMMDD/` | the ARAX host (dev, test and production endpoints). ARAX runs in Docker as `rt`; containers mount `/translator/data` at the same path. Mode `777`. Bastion and IP allowlist. |
| sftp.transltr.io | `team-expander-USERNAME` | `databases/tier0-YYYYMMDD/`, `md5_sums/tier0-YYYYMMDD/` (under `/transltr-063208468694-sftp/team-expander`) | the ITRB drop. ITRB builds the transltr.io (CI, test, production) images from it after the PR is merged (the mechanism is ITRB's; see [7b](#7b-check-the-itrb-ci-deployment)). SFTP only with S3 behind it: no shell, no md5sum, no rename, no resumed uploads. |
| kg2webhost.rtx.ai | `ubuntu` | `/home/ubuntu/drugbank.xml` | DrugBank source. DrugBank isn't redistributable, so it's never staged or committed. |

---

## Reference: autobuild configuration

Shared settings are in `config/config.yaml`. Everything machine- and person-specific is in `config/config.local.yaml`, which isn't tracked and overrides the shared file key by key; start from `config/config.local.example.yaml`.

### Paths and inputs

| Key | Meaning | Example |
|---|---|---|
| `graph_releases` | where Tier0 releases are downloaded from | `https://kgx-storage.ci.transltr.io/releases/translator_kg` |
| `graph_root` | Tier0 graphs, one `YYYYMMDD/` each | `/data/translator_kg` |
| `graph_version` | the graph to build, as `YYYYMMDD`; unset means the latest release | `"20260928"` |
| `babel_dir` | Babel sqlites, `babel-YYYYMMDD[-pN].sqlite` | `/data/babel` |
| `babel_releases` | GitHub repo the Babel sqlites are released from | `Translator-CATRAX/stitch-proj` |
| `pubmed_dir` | PubMed mirror root | `/data/pubmed` |
| `output_root` | build output, one `YYYYMMDD/` each | `/data/arax_databases` |
| `webhost` | SSH destination with DrugBank | `ubuntu@kg2webhost.rtx.ai` |
| `webhost_drugbank` | DrugBank's path on it | `/home/ubuntu/drugbank.xml` |
| `drugbank_dir` | local DrugBank downloads | `/data/drugbank` |
| `drugbank_version` | pin a DrugBank version (skips the webhost check); builds so far pin `"2023-01-04"` | `"2023-01-04"` |
| `rtx_config_dbs` | your RTX checkout's `config_dbs.json`, the template; never modified | `/path/to/RTX/code/config_dbs.json` |
| `arax_db_root` | where ARAX reads databases from. **Don't change.** | `/translator/data/orangeboard/databases` |

### `staging`

| Key | Meaning |
|---|---|
| `staging.targets.<name>` | one block per target; the name becomes `staged/<name>.json` and `logs/staged_<name>.log` |

SSH targets (`arax_databases`, `cicd`, `arax_ncats`):

| Key | Meaning | Default |
|---|---|---|
| `host` | SSH destination or `~/.ssh/config` alias | required |
| `root` | directory holding releases | required |
| `dir_mode` | release directory permissions | `775` |
| `sudo` | create and rename the release directory with `sudo -n` | `false` |
| `read_check` | command prefix that runs as the user ARAX reads files as; staging fails unless that user can read every file. `arax_ncats` uses `sudo -n docker exec rtx2 sudo -u rt` | none |

SFTP targets (`itrb_sftp`):

| Key | Meaning | Default |
|---|---|---|
| `sftp` | `true` marks an SFTP-only target | `false` |
| `host` | SFTP destination: **your own** team-expander login | required |
| `databases` | directory for releases' artifacts | `databases` |
| `md5_sums` | directory for releases' md5 files | `md5_sums` |

`arax_databases` is required: the PR step reads its `host` and `root` to check the files are there.

### `rtx_pr`

| Key | Meaning |
|---|---|
| `repo` | GitHub repo for `gh` (`RTXTeam/RTX`) |
| `remote` | git URL to clone and push (`git@github.com:RTXTeam/RTX`) |
| `base` | branch the PR targets (`master`) |
| `branch_prefix` | prefix of the new branch (`autobuild/`) |

### `build_record`

| Key | Meaning |
|---|---|
| `remote` | remote to push to (`origin`) |
| `branch` | branch to push (`master`) |

### Release flags

Nothing in the config starts a release. Passing a flag to `./autobuild` is what authorizes that action:

| Goal | Command |
|---|---|
| Full rollout | `./autobuild --full` |
| Build and test only | `./autobuild --build` |
| Rehearse the release | `./autobuild --rehearse --full` |
| One step | `./autobuild --stage`, `--pr` or `--record` |

- `--record` stops unless every server is staged and there's a PR.
- `--pr` stops unless every artifact is on `arax_databases`. A rehearsal reports that instead of stopping.

---

## Reference: autobuild inputs

The Babel and Biolink versions are **derived from the graph's metadata**, so they always match it. DrugBank and PubMed are independent of the graph: DrugBank is pinned or the newest available, and PubMed is whatever NCBI has when the build directory's mirror step runs. The choices are printed every time Snakemake starts. The build report records the graph, Babel version (not the patch), Biolink version and PubMed baseline; record the rest yourself ([2e](#2e-review-the-build-report)).

| Input | Chosen by | Fetched automatically? |
|---|---|---|
| Tier0 graph | `graph_version`, or the latest release | yes, if not local |
| Babel | the graph's `babelVersion`, highest patch | yes, if not local |
| Biolink | the graph's `biolinkVersion` | yes (cached) |
| DrugBank | newest of local vs. webhost, or `drugbank_version` | yes, if not local |
| PubMed | NCBI's current baseline and updates | yes, refreshed once per build directory |

### Tier0 graph

`graph_version` names the graph as `YYYYMMDD`, the date in its release's `build_date`; unset means the release the server marks as latest. A complete `{graph_root}/YYYYMMDD/` is used as it is. Otherwise `fetch_graph` checks free disk, downloads the archive with resume, checks its size, unpacks it beside the destination and moves the three files in. The server publishes no checksums, so a damaged archive is only caught by its size or by failing to unpack. The files are `protected`, and nothing is overwritten. If the server can't be reached, an unset `graph_version` falls back to the newest complete local graph with a warning.

`--stage`, `--pr` and `--record` without `--build` use the newest build in `output_root` when `graph_version` is unset, so a new release can't redirect them.

### Babel

1. Reads `translator:babelVersion` (or `babelVersion` in older metadata) from `graph-metadata.json`, e.g. `2026jul22`.
2. Lists the `babel_releases` (stitch-proj) releases on GitHub. Each release body names its Babel source (`babel_outputs/2026jul22/`), the sqlite URL and its MD5.
3. Patched releases (`-p1`, `-p2`, …): the **highest patch wins**, superseding an older local patch.
4. A matching file in `babel_dir` is used as is. Otherwise `fetch_babel` downloads it (~185–220 GB): checks free disk, downloads to `.part` with resume (`curl --continue-at -`), checks the MD5 (deleting a bad copy), then renames it into place. The file is `protected` (read-only).
5. If GitHub is unreachable, the newest local patch is used with a warning. If GitHub is reachable but no release matches, the build stops, **even if a matching sqlite is already in `babel_dir`**.

Local names are `babel-YYYYMMDD.sqlite` or `babel-YYYYMMDD-pN.sqlite`, where `YYYYMMDD` is the Babel version as a date (`2026jul22` → `20260722`).

### Biolink

`translator:biolinkVersion` (or `biolinkVersion`) from `graph-metadata.json`. BiolinkHelper caches the model in `{output_root}/.cache/biolink/`, shared by all builds and the tests.

### DrugBank

- A file's version is its `exported-on` date, from the XML root element. Local copies are `drugbank-YYYY-MM-DD.xml` in `drugbank_dir`.
- With `drugbank_version` set, that version is used and the webhost isn't asked. Otherwise the newest of local and webhost wins (reading the first 1000 bytes of the webhost's file over SSH); if the webhost is unreachable, the newest local copy is used with a warning.
- `fetch_drugbank` (only when the version isn't local) checks the webhost still has that version, copies it with `rsync --partial`, compares md5s with the webhost's `md5sum` and deletes a bad copy. The file is `protected`.

### PubMed

`pubmed_mirror` runs `mirror_pubmed.py` once per build directory (its only output is that build's `pubmed_manifest.json`, so it doesn't run again once the manifest exists), mirroring `ftp.ncbi.nlm.nih.gov/pubmed/{baseline,updatefiles}/` into `{pubmed_dir}/ftp.ncbi.nlm.nih.gov/pubmed/`:

- downloads files that are missing or whose size or mtime differs from the server's
- verifies each against NCBI's `.md5` (writing to `.part` first), retrying up to 5 times
- sets verified files' mtime to the server's so later runs skip them without hashing
- **deletes** local files no longer on the server, so the yearly baseline rollover (`pubmed26n*` → `pubmed27n*`) doesn't leave old files to be parsed twice
- writes `pubmed_manifest.json`: baseline release, file counts, downloads, removals, start and finish times

The first run downloads ~1,650 files (~70 GB, hours); later runs usually take seconds. To compare the mirror with the server without changing anything (exit 0 if current, 1 if not):

```bash
python workflow/scripts/curie_pmids/mirror_pubmed.py --dest /data/pubmed --check
```

---

## Reference: autobuild steps

Steps 1–8 are rules in `workflow/rules/`. Paths are relative to `{output_root}/YYYYMMDD/`. Artifact rules write `logs/<artifact>.log` and `benchmarks/<artifact>.tsv` (time and memory).

| # | Rule | File | Does | Output |
|---|---|---|---|---|
| 1 | `fetch_graph` | `sources.smk` | download the Tier0 graph (only if not local) | `{graph_root}/YYYYMMDD/` |
| 1 | `fetch_babel` | `sources.smk` | download Babel (only if not local) | `{babel_dir}/babel-*.sqlite` |
| 1 | `fetch_drugbank` | `sources.smk` | download DrugBank (only if not local) | `{drugbank_dir}/drugbank-*.xml` |
| 2 | `pubmed_mirror` | `curie_pmids.smk` | refresh the PubMed mirror | `pubmed_manifest.json` |
| 3 | `autocomplete` | `autocomplete.smk` | build autocomplete | artifact |
| 3 | `overlay` | `overlay.smk` | build tier0-info-for-overlay | artifact |
| 3 | `curie_to_pmids` | `curie_pmids.smk` | build curie_to_pmids (all cores) | artifact |
| 3 | `fda_drugs` | `fda_drugs.smk` | build fda_approved_drugs | artifact |
| 4 | `curie_ngd` | `curie_ngd.smk` | build curie_ngd (all cores) | artifact |
| 5 | `test_artifacts` | `report.smk` | pytest against the five artifacts | `test_results/artifacts.xml` |
| 6 | `md5` | `md5.smk` | one md5 per artifact, **only after the tests pass** | `md5_sums/*.md5` |
| 7 | `arax_config_dbs` | `arax_config.smk` | write the new `config_dbs.json` | `config_dbs.json` |
| 7 | `test_config_dbs` | `report.smk` | pytest against it | `test_results/config_dbs.xml` |
| 8 | `report` | `report.smk` | report and counts | `build_report.md`, `artifact_stats.json` |
| 9 | `--stage` | `tools/stage_artifacts.py` | stage on each target | `staged/<target>.json` |
| 10 | `--pr` | `arax/open_config_pr.py` | open or update the RTX PR | `rtx_pr.json` |
| 11 | `--record` | `report/record_build.py`, `report/push_build_record.py` | copy the record into the repo, then commit and push it | `builds/YYYYMMDD/`, `record_pushed.json` |

Steps 9–11 aren't Snakemake rules: `./autobuild` runs them only when asked. Scratch files go in `work/` and are deleted by the rules that make them.

**`arax_config_dbs`** (`arax/update_config_dbs.py`) copies `rtx_config_dbs` and sets five `database_downloads` entries to `{arax_db_root}/tier0-YYYYMMDD/<file>`: `info_for_overlay_sqlite` (tier0-info-for-overlay), `autocomplete`, `curie_to_pmids`, `curie_ngd` and `fda_approved_drugs`. Every other entry is copied unchanged, with RTX's 3-space indent to keep the diff small. It fails if one of those keys isn't in the template.

### What triggers a rebuild

Snakemake rebuilds an output when an input is missing or newer, or when the rule's params or code changed (so editing, say, `arax_db_root` or a staging target's settings reruns those rules). Scripts are inputs (except the fetch scripts, so editing them doesn't invalidate downloads).

| Change | Rebuilt |
|---|---|
| new Tier0 graph (or, with `graph_version` unset, a new release) | everything (new build directory) |
| new Babel patch | curie_to_pmids, curie_ngd, fda_approved_drugs, tests onward |
| new DrugBank | fda_approved_drugs, tests onward |
| new PubMed files at NCBI | nothing: the mirror is refreshed only when a build directory has no `pubmed_manifest.json` yet |
| a builder script edited | that artifact (plus curie_ngd for curie_to_pmids), tests onward |
| a test edited | tests onward |
| RTX's `config_dbs.json` changed | `config_dbs.json`, its test, report onward |

"Onward" stops at the report: staging and the PR only happen when asked for. Once a build is staged or proposed, `./autobuild --build` won't rebuild it; name an artifact to build it as a new version ([Rebuild one artifact](#rebuild-one-artifact)).

---

## Reference: autobuild outputs

### The build directory

```
{output_root}/YYYYMMDD/
├── autocomplete_v1.0_tier0-YYYYMMDD.sqlite
├── tier0-info-for-overlay_v1.0_tier0-YYYYMMDD.sqlite
├── curie_to_pmids_v1.0_tier0-YYYYMMDD.sqlite
├── curie_ngd_v1.0_tier0-YYYYMMDD.sqlite
├── fda_approved_drugs_v1.0.pickle
├── md5_sums/<artifact>.md5
├── config_dbs.json            RTX's file, pointed at this build
├── pubmed_manifest.json       the PubMed snapshot used
├── test_results/              artifacts.xml, config_dbs.xml (JUnit)
├── artifact_stats.json        counts, for the next build to compare
├── build_report.md
├── staged/<target>.json       one per staging target
├── rtx_pr.json                the PR's branch and URL
├── record_pushed.json         the record push result
├── vX.Y/                      a rebuilt artifact, with its own md5_sums/, logs/, config_dbs.json
├── logs/                      one per step (except md5)
└── benchmarks/                time and memory per artifact step
```

A new build always names its artifacts `v1.0`. `v1.1` and up are only for an artifact rebuilt after its build was released, because ARAX only downloads a database when its filename changes.

### The build report

| Section | Contents |
|---|---|
| header | build version; when and where it was written |
| Inputs | Tier0 version and date, PubMed baseline, Babel version, Biolink version |
| Run | total build time; test totals |
| Artifacts | size, step time, peak memory (PSS), finish time |
| Counts | key counts per artifact vs. the previous build: old, new, change, % |
| Release | (in the build record only) staging results and the RTX PR |

The previous build is the newest older `builds/YYYYMMDD/` with an `artifact_stats.json`. (`builds/20260621/` has only that file: it predates the report and was added for comparison.) To write stats for an old build: `python workflow/scripts/report/artifact_stats.py --output builds/YYYYMMDD/artifact_stats.json --artifact autocomplete=PATH --artifact tier0_info_for_overlay=PATH --artifact curie_to_pmids=PATH --artifact curie_ngd=PATH --artifact fda_approved_drugs=PATH`.

### Result files

| File | Contents | Meaning |
|---|---|---|
| `staged/<target>.json` | `{"staged": true, "location", "already_staged": false}` | uploaded and verified this run |
| | `{"staged": true, "location", "already_staged": true}` | already there; verified; nothing uploaded |
| `rtx_pr.json` | `{"dry_run": false, "branch", "url"}` | PR opened or updated |
| `record_pushed.json` | `{"committed": true, "commit"}` | pushed |
| | `{"committed": false}` | already committed |

A rehearsal writes none of these. A server's result is only written once it has every artifact, because `--record` relies on it.

---

## Reference: autobuild tests

The tests read the artifacts the way ARAX, Pathfinder and xCRG do, with the same queries where possible. They are **local only**: no Plover, Retriever or other service.

| File | Checks |
|---|---|
| `test_autocomplete.py` | schema; prefix and substring completion with rtxcomplete's query verbatim; every graph node searchable; no empty or case-duplicate terms |
| `test_overlay.py` | schema; the Fisher exact test's category-count query; category counts follow the Biolink hierarchy; neighbor counts; neighbor IDs are graph nodes; edge publications |
| `test_curie_to_pmids.py` | schema; PMIDs are sorted unique int32 blobs in range; xCRG's read path; Tier0 edge PMIDs included; the name-collision override; CURIEs are Babel-preferred; ARAX lookups of hubs |
| `test_curie_ngd.py` | schema; entry format; `pmid_length` and NGD agree with curie_to_pmids (NGD against a reference ported from RTX's `test_compute_ngd.py`); every CURIE in curie_to_pmids; no missing rows; Pathfinder and xCRG read paths; a known pair |
| `test_fda_approved_drugs.py` | a set of CURIEs; Babel-preferred; contains known approved drugs |
| `test_config_dbs.py` | required keys; built entries point at this build's files and have md5s; everything else carried over; no sqlite sidecar files |

A failure stops the build before the md5s are written, so nothing is staged and no PR is opened.

Run by hand against a build (each fixture globs for its artifact, so `--build-dir` must hold exactly one of each; `--babel` must be the file the build used):

```bash
python -m pytest tests -p no:cacheprovider --ignore tests/test_config_dbs.py \
    --build-dir /data/arax_databases/YYYYMMDD \
    --graph-dir /data/translator_kg/YYYYMMDD \
    --babel /data/babel/babel-YYYYMMDD.sqlite \
    --biolink-cache-dir /data/arax_databases/.cache/biolink

python -m pytest tests/test_config_dbs.py -p no:cacheprovider \
    --build-dir /data/arax_databases/YYYYMMDD \
    --rtx-config-dbs /path/to/RTX/code/config_dbs.json
```

---

## Reference: staging, the RTX PR and the build record

Staging means putting the tested artifacts on every server **before** anything points at them: once master's `config_dbs.json` points at `tier0-YYYYMMDD`, every ARAX instance and ITRB image goes looking for them.

### SSH targets (`tools/stage_artifacts.py`)

1. Checks the local artifacts against their md5 files. Anything that hasn't passed its tests is reported as missing, and you choose whether to build it, stage the rest, or cancel.
2. Compares with `{root}/tier0-YYYYMMDD`: files already there with the right md5 are skipped. **A file there with a different md5 stops it**; nothing is overwritten. Other files, such as xDTD, are ignored.
3. Checks there's room for the files plus 10 GB.
4. With `--rehearse`, reports and stops.
5. Creates `{root}/tier0-YYYYMMDD/` and `{root}/.staging-tier0-YYYYMMDD-files/` (with `sudo -n` if `sudo: true`), owned by the SSH user, mode `dir_mode`.
6. Uploads to the hidden directory with `rsync --partial`, or `scp` if the server has no rsync. Files are mode 644.
7. Checks every md5 on the server, moves each file into place with `mv -n`, and checks the md5s and read access again.

Nothing on any server is ever deleted.

### The ITRB SFTP drop (`tools/stage_artifacts.py`)

No shell, so checks are by size only. A matching size doesn't prove matching contents; the md5 files are what ITRB checks contents against.

1. Lists `databases/tier0-YYYYMMDD/` and `md5_sums/tier0-YYYYMMDD/`.
2. Files already there at the right size are skipped. **A file there at the wrong size stops it**, including one cut short by an interrupted upload (it isn't resumed or replaced). To recover, have the file removed from the drop (ITRB, or `rm` in `sftp` if your account allows it) and rerun. Other files, such as xDTD, are ignored.
3. With `--rehearse`, prints the plan and stops.
4. Uploads the artifacts first and the md5 files last, so a release whose md5 files are all present is complete.
5. Checks the uploaded files' sizes.

### The RTX PR (`arax/open_config_pr.py`)

1. Refuses branch names `master`, `main`, `production`, `itrb-test`, `dev`, and a base equal to the branch.
2. Looks up every autobuild PR. **Stops** if another build's is open (one build at a time).
3. **Stops** unless every artifact is on `arax_databases` with matching md5s (the PR's CI downloads from there). A rehearsal reports this instead.
4. Decides what to do from this build's PRs, on `autobuild/tier0-YYYYMMDD` and any `-update-N` branches:

   | State | What it does |
   |---|---|
   | one is open | pushes to its branch, which updates the PR; a new commit means rerunning CI and the dev-machine tests |
   | the newest was merged | opens a new PR on the next `-update-N` branch from the base, if anything changed. **Stops** if the base is on a different Tier0. |
   | closed unmerged, or none | opens a new PR on the first free name. **Stops** if the base is on a newer Tier0. |

5. Starts from the target branch's own `code/config_dbs.json` and changes only the entries this pipeline builds, so Phase 4 changes and other work are kept. **Stops** if one of those keys is missing.
6. With `--rehearse`, prints the diff and stops.
7. Commits only that file as "ARAX autobuild pipeline" (with your `git config user.email`) and pushes by explicit refspec (never forced). A new PR's body says it's for human review, where the files are staged, the diff, and the build report.

It never merges, approves, deploys or deletes anything.

### The build record (`report/record_build.py`, `report/push_build_record.py`)

`--record` first copies `artifact_stats.json`, `config_dbs.json`, `pubmed_manifest.json`, `md5_sums/`, `test_results/`, `benchmarks/`, `logs/`, `staged/`, `rtx_pr.json`, and the report with a Release section added, into `builds/YYYYMMDD/` in arax-build-automation. A rebuild's `vX.Y/` records go in a subdirectory, without their data files. Logins are left out of the staging results and logs, because this repo is public.

Then, the only step that writes to that repo:

1. **Stops** unless every server is staged and there's a PR.
2. **Stops** unless the checkout is on `build_record.branch`, and unless it has no commits that aren't on the remote. If it's behind, it fast-forwards.
3. Does nothing if the record is already committed.
4. Commits only `builds/YYYYMMDD/` (pathspec commit) as "ARAX autobuild pipeline", message `Build record for Tier0 build YYYYMMDD` with the PR URL, checks the commit contains nothing else, and pushes normally (never forced).

---

## Common operations

### Run part of the pipeline

| Goal | Command |
|---|---|
| see the plan | `./autobuild` |
| everything | `./autobuild --full` |
| build and test | `./autobuild --build` |
| one artifact | `./autobuild --build curie_ngd` |
| stage on every target | `./autobuild --stage` |
| stage on some targets | `./autobuild --stage cicd arax_ncats` |
| the RTX PR | `./autobuild --pr` |
| the build record | `./autobuild --record` |
| rehearse any of these | add `--rehearse` |
| why each build step would run | `source .venv/bin/activate`, then `snakemake -n` |

### Update a build's PR

Just rerun `./autobuild --pr`. It pushes to the branch of an open PR, or opens a new one if the last was closed or merged. If `gh pr create` failed after a push, the leftover branch is harmless: a rerun opens the PR from the next `-update-N` branch.

### Recover a half-finished build-record push

`--record` commits, then pushes. If the push fails, a rerun stops with `master has 1 commit(s) not on origin/master`. Don't drop that commit. Check it holds only the record, then push it yourself:

```bash
git log origin/master..master --stat     # one "Build record for Tier0 build YYYYMMDD" commit, only builds/YYYYMMDD/
git push origin master
./autobuild --record      # sees the record is committed and writes record_pushed.json
```

### Build an older graph

Set `graph_version: "YYYYMMDD"` (its directory must be complete) and run.

### Rebuild one artifact

**Before the build is staged:** save your change and run `./autobuild --build curie_ngd`. It rebuilds that artifact and reruns the tests onward. An edited builder script is also picked up by a plain `./autobuild --build`.

**After the build is staged**, its files can't change: the servers have a `v1.0` with a different md5, and ARAX wouldn't download a same-named file anyway. Run the same command, and it offers to build the next unused version in `{output_root}/YYYYMMDD/vX.Y/`, leaving the old file in place:

```bash
./autobuild --build curie_ngd     # asks, then builds and tests e.g. curie_ngd_v1.1_tier0-YYYYMMDD.sqlite
./autobuild --stage               # uploads it to every server
./autobuild --pr                  # points config_dbs.json at it, on the open PR or a new one after a merge
./autobuild --record              # records it under builds/YYYYMMDD/v1.1/
```

- `--new-version` skips the question; `-y` alone won't create a version.
- Versions are numbered across the whole build, checking both local files and `arax-databases`, so a later rebuild of another artifact gets the next number.
- Rebuilding `curie_to_pmids` also rebuilds `curie_ngd`. Declining cancels the rebuild. It uses the PubMed mirror as it is now.
- It only runs that artifact's tests.
- A rebuild that fails its tests leaves its `vX.Y/` directory, and the next attempt uses the next version.
- For `fda_approved_drugs`, or any artifact whose link line you rely on, also update `code/generate-db-symlinks.sh` by hand.

### Stage a single file by hand

For a rebuilt artifact or xDTD. The file goes into the existing `tier0-YYYYMMDD/` on each server, next to the others; nothing reads a new filename until `config_dbs.json` points at it. **Never copy over a file that's already there:** a plain `scp FILE dir/` or SFTP `put` silently replaces a same-named file, so each recipe below checks the name is free, uploads to a temporary name, verifies the md5, and only then gives it its final name (`mv -n` refuses to overwrite). Do arax-databases first, since arax.ncats.io copies from it.

`FILE` is the filename and `MD5` its expected md5: PSU's value for xDTD, or the one in the `.md5` you wrote for a rebuilt artifact.

**arax-databases.rtx.ai:**

```bash
H=rtxconfig@arax-databases.rtx.ai D=/home/rtxconfig/tier0-YYYYMMDD
ssh $H "test ! -e $D/FILE" || echo "STOP: FILE already exists"
scp FILE $H:$D/.FILE.part
ssh $H "cd $D && echo 'MD5  .FILE.part' | md5sum -c && chmod 644 .FILE.part && mv -n .FILE.part FILE && ls -l FILE"
```

**cicd.rtx.ai** (the release directory is mode 777, so no sudo): the same three commands with `H=ubuntu@cicd.rtx.ai D=/mnt/data/orangeboard/databases/tier0-YYYYMMDD`.

**arax.ncats.io**, copied from arax-databases as `rt` inside the container, so ARAX's user owns it:

```bash
ssh arax
sudo docker exec -it rtx2 bash
su - rt
cd /translator/data/orangeboard/databases/tier0-YYYYMMDD
test ! -e FILE || echo "STOP: FILE already exists"
scp rtxconfig@arax-databases.rtx.ai:/home/rtxconfig/tier0-YYYYMMDD/FILE .FILE.part
echo 'MD5  .FILE.part' | md5sum -c && mv -n .FILE.part FILE && ls -l FILE
exit; exit; exit
```

**sftp.transltr.io**: there's no md5sum or rename, so check the name is free, upload under the final name, and compare sizes with your local `ls -l`. The file goes first and its `.md5` last. Uploading here also makes any later autobuild SFTP staging run for this build stop ([SFTP behavior](#the-itrb-sftp-drop-araxstage_sftppy)). For a whole release at once, see also the [Config, databases, and SFTP](https://github.com/RTXteam/RTX/wiki/Config,-databases,-and-SFTP#steps-for-all-databases-at-once) wiki page.

```
sftp team-expander-USERNAME@sftp.transltr.io
cd databases/tier0-YYYYMMDD
ls -l            # FILE must not be listed
put FILE
ls -l            # FILE's size must equal the local size
cd ../../md5_sums/tier0-YYYYMMDD
ls -l            # FILE.md5 must not be listed
put FILE.md5
ls -l
bye
```

### Point ARAX at a new file by hand

```bash
cd ~/RTX-rollout     # not the rtx_config_dbs checkout (see Phase 4)
git switch master && git pull
git switch -c curie_ngd-v1.1-tier0-YYYYMMDD
# edit only that artifact's line under database_downloads in code/config_dbs.json
# (for tier0-info-for-overlay, the key is info_for_overlay_sqlite)
git diff
git commit -am "Use curie_ngd_v1.1_tier0-YYYYMMDD.sqlite"
git push -u origin curie_ngd-v1.1-tier0-YYYYMMDD
gh pr create --base master --fill
```

The branch doesn't start with `autobuild/`, so it doesn't block autobuild's own PRs. After it's merged, roll it out as in [Phase 7](#phase-7-production-rollout).

### Free disk space

On the autobuild machine, once a build is released and merged, you can delete old build directories in `output_root` (the record is in `builds/`), old graphs, Babel sqlites no current graph uses (they're read-only; `rm -f`) and old DrugBank files. Keep the PubMed mirror: re-downloading it takes hours.

On the servers, see [Phase 8](#phase-8-final-cleanup), and always post in `#deployment` first.

---

## Troubleshooting

For an autobuild failure, find the failed rule in Snakemake's output, read its log in `{output_root}/YYYYMMDD/logs/`, fix the cause and rerun; finished steps aren't redone.

| Error | Cause and fix |
|---|---|
| `No module named 'stitch'` (or `biolink_helper_pkg`, `yaml`) | running something by hand without the venv: `source .venv/bin/activate` |
| `Missing settings: ...` | set them in `config/config.local.yaml`; see [Configure autobuild](#configure-autobuild) |
| `No release on ... was built on ...` | `graph_version` has no release; fix it, or remove it to use the latest |
| `... already has nodes.jsonl` | an incomplete graph directory; move those files away so it can download |
| `... needs about N GiB but ...` | not enough disk for the graph; free space and rerun, and the download resumes |
| `Couldn't unpack ...`, `should be N bytes` | damaged graph download, already deleted; rerun |
| `... records no babelVersion` / `biolinkVersion` | `graph-metadata.json` is wrong or truncated; download it again |
| `Unrecognized babelVersion` | not of the form `2026jul22`; `sources/babel.py` needs updating |
| `No ... release was built from Babel <version>` | stitch-proj has no sqlite for it yet; ask for one. A local copy in `babel_dir` doesn't get past this: GitHub is always checked, and a local file is used without a release only when GitHub can't be reached |
| `... needs N GiB more but ... has N GiB free` | not enough disk for Babel; free space |
| `md5 mismatch for ...; deleted the bad copy` | corrupted download; rerun |
| `webhost DrugBank is no longer the ... release; rerun the build` | the webhost's file changed mid-run; rerun |
| `DrugBank: no version found locally or on the webhost` | webhost unreachable and nothing local; check SSH |
| `LockException` | another Snakemake is running, or a stale lock; if none is running, `./autobuild --unlock` |
| `ProtectedOutputException` | a rule wants to rewrite a protected download; if intended, `chmod u+w` and delete it |
| a step killed with no error, or `MemoryError` | out of RAM. For curie_ngd, use fewer cores (`-c12`). curie_to_pmids ignores `-c` (its pools use every core but one), so for it free memory or run on a machine with more RAM per core |
| a test failed | see `logs/test_artifacts.log`, `test_results/artifacts.xml`; nothing can be staged |
| `... is not in database_downloads of ...` | RTX renamed a key; update `arax_config.smk`, `test_config_dbs.py` and `BUILT_KEYS` in `arax/open_config_pr.py` |
| `Permission denied (publickey)`, `Host key verification failed` | SSH doesn't work non-interactively for that host; run the [checks](#ssh-setup) |
| `sudo: a password is required` | cicd needs passwordless sudo; arax needs passwordless `sudo docker` |
| `no longer matches ...` | an artifact changed after its md5; rebuild it with `./autobuild --build` |
| `already has different copies of: ...` | a `tier0-YYYYMMDD/` from a bad attempt: if nothing uses it, remove the files by hand and rerun; if it's live, see [Rebuild one artifact](#rebuild-one-artifact) |
| `Only N GB free on ...` | the server is short of space; see Phase 1d |
| `md5 mismatch after upload` | corrupted upload; rerun (the staging directory resumes) |
| `Not readable through ...` | ARAX's user can't read the files on arax.ncats.io; check permissions |
| `... already exists at a different size` (SFTP) | a partial upload or conflicting file; have it removed and rerun |
| `Another build's autobuild PR is still open` | one build at a time; merge or close it |
| `Not staged on ... with matching md5s` | stage first: `./autobuild --stage arax_databases` |
| `master's config_dbs.json already matches this build` | RTX already points at this build |
| `Can't publish the build record yet` | stage on every server and open the PR first |
| `The checkout is on '...', not 'master'` | switch arax-build-automation to `master` |
| `master has N commit(s) not on origin/master` | push or drop your own commits first |
| CI can't find a database | see [5a](#5a-ci) |
| ITRB pod crash-loops | a missing or wrong md5 on the SFTP drop; check `md5_sums/tier0-YYYYMMDD/` has one correct file per database, including xDTD |

---

## Developing autobuild

### Add an artifact

1. Write the builder under `workflow/scripts/<name>/`.
2. Add its path in `workflow/Snakefile` (`BUILD_DIR / f"<name>_v1.0_{TIER0}.<ext>"`) and add it to `RELEASE_ARTIFACTS` (so it gets an md5 and is staged).
3. Add a rule in `workflow/rules/<name>.smk` with `log: artifact_log(...)` and `benchmark: artifact_benchmark(...)`, and `include:` it in `workflow/Snakefile`.
4. Add a `--set <key>=...` in `arax_config.smk`.
5. Add a fixture in `tests/conftest.py`, a `tests/test_<name>.py`, and its key to `BUILT_KEYS` and `REQUIRED_KEYS` in `tests/test_config_dbs.py`.
6. Add its counts to `report/artifact_stats.py` (a function plus an entry in `KINDS`) and an `--artifact` in `report.smk`.
7. Update this page.

### Add or change a staging target

Add a block under `staging.targets` ([configuration](#staging)); set `sftp: true` for an SFTP-only one. Names must be unique. Try it with `./autobuild --rehearse --stage NAME` first.

### Update dependencies

```bash
uv add 'PACKAGE==VERSION'     # e.g. uv add 'numpy==2.5.3'; or edit pyproject.toml, then:
uv lock && uv sync
```

Then run at least the tests against the last build.

### Keep in sync with RTX

- The overlay edge key must match `ARAXQuery/util.get_arax_edge_key`.
- The tests copy RTX's read paths; if ARAX changes how it reads an artifact, update the test.
- If RTX renames a `database_downloads` key, update `arax_config.smk` and `test_config_dbs.py`.

---

## Copy-pasteable issue checklist

```markdown
**Tier0 build:** YYYYMMDD  **autobuild PR:**   **Build report:**

Kick off
- [ ] Tier0 graph downloaded to graph_root/YYYYMMDD (assignee: )
- [ ] PSU told the stamp and graph location; xDTD rebuild vs. refresh decided (assignee: )
- [ ] ≥100 GB free on arax-databases.rtx.ai and arax.ncats.io (#deployment notified before any deletion)

autobuild
- [ ] `./autobuild --rehearse --full` checked (versions, servers, PR diff) (assignee: )
- [ ] `./autobuild --full` finished: autocomplete, tier0-info-for-overlay, curie_to_pmids, curie_ngd, fda_approved_drugs built, tested and staged on:
    - [ ] arax-databases.rtx.ai
    - [ ] cicd.rtx.ai
    - [ ] arax.ncats.io
    - [ ] sftp.transltr.io (with md5s)
- [ ] RTX PR opened; build report reviewed (counts vs. previous build)
- [ ] curie_to_pmids log: 0 parse failures; resolve errors and recognized names in line with the previous build
- [ ] Recorded: Babel file (with patch), DrugBank version, RTX template commit
- [ ] FDA set compared with master's: removed __ added __ (if changed: new filename, see Integration)

ExplainableDTD
- [ ] `ExplainableDTD_v1.0_tier0-YYYYMMDD-all_with_paths.db` built (assignee: ; subissue: )
- [ ] Staged on arax-databases.rtx.ai (PSU)
- [ ] PSU's md5 recorded: 
- [ ] Copied to cicd.rtx.ai, arax.ncats.io, sftp.transltr.io (+ md5), each checked against PSU's md5 (assignee: )

Integration (on autobuild/tier0-YYYYMMDD)
- [ ] `explainable_dtd_db` in config_dbs.json
- [ ] FDA set changed? new `fda_approved_drugs_v1.N.pickle` staged on all four servers; config_dbs.json and generate-db-symlinks.sh updated
- [ ] ARAX_database_manager.py (only if key names changed)
- [ ] No hardcoded previous Tier0 stamp left (`git grep`)
- [ ] OpenAPI version / biolink-version bumps (confirm with @edeutsch)
- [ ] `LATEST_TIER0_VER` in code/generate-db-symlinks.sh

Testing
- [ ] master merged into the branch first; tested commit SHA: 
- [ ] Test Build on cicd.rtx.ai passes on that commit (run link: )
- [ ] pytest on a development machine (clean checkout, RTX venv, databases, KP cache)
- [ ] Flask server: four example queries, results and TRAPI logs
- [ ] arax.ncats.io/test: re-test, STDERR, Background Tasker
- [ ] Tier0 smoke check (Tier0-only CURIE, Synonyms tab)

Merge
- [ ] master tagged tier0-<previous YYYYMMDD>
- [ ] Branch head is the tested commit (or CI and dev pytest rerun on the new head)
- [ ] Sign-off: @hodgesf @bazarkua @saramsey @dkoslicki @edeutsch
- [ ] PR merged; merge method: ; commit SHA(s): 

Rollout
- [ ] Endpoint inventory written (container, checkout, service, port, Python, commit before/after)
- [ ] ITRB deployment procedure obtained from its owner and recorded
- [ ] master on arax.ncats.io dev endpoints (#deployment notified; occupancy checked); pytest on each
- [ ] ITRB CI deployed commit/image verified; live queries against CI; pytest on the deployed commit (method: )
- [ ] Production endpoints one at a time; one legacy endpoint kept
- [ ] ITRB test and production promoted and verified

Cleanup
- [ ] Changelog entry
- [ ] autobuild branch deleted (no CI run or preview still using it)
- [ ] After a week: last legacy endpoint moved
- [ ] Reference check done; previous release removed from arax-databases and arax.ncats.io (#deployment notified; keep SFTP copy)
- [ ] Issue closed with a retro
```
