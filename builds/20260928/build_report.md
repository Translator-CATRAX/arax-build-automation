# ARAX database build 20260928

Written 2026-10-06 12:15 PDT on eng-kec3048-45.

## Inputs

- Tier0 graph: 1.0.2 2026-09-28
- PubMed: pubmed26
- Babel: 2026jul22
- Biolink model: 4.4.4

## Run

- Build time: 0:45:26, from the first step starting (2026-10-06 09:09) to the last one finishing (2026-10-06 09:55)
- Tests: 42 passed, 0 failed, 0 skipped in 10 s (`test_results/artifacts.xml`, `test_results/config_dbs.xml`)

## Artifacts

Step time and peak memory (PSS) are for the rule that built the artifact.

| Artifact | Size | Step time | Peak memory | Built |
|---|---:|---:|---:|---|
| autocomplete | 350.8 MB | 0:00:10 | 0.4 GB | 2026-10-06 09:55 |
| tier0-info-for-overlay | 3.2 GB | 0:03:13 | 4.4 GB | 2026-10-06 09:51 |
| curie_to_pmids | 1.6 GB | 0:38:27 | 16.9 GB | 2026-10-06 09:48 |
| curie_ngd | 1.5 GB | 0:02:33 | 11.8 GB | 2026-10-06 09:55 |
| fda_approved_drugs | 75.1 KB | 0:00:18 | 8.7 GB | 2026-10-06 09:49 |
| config_dbs | 1.2 KB |  |  |  |

## Counts

Compared with the previous build, 20260621.

| Artifact | Count | 20260621 | 20260928 | Change | % |
|---|---|---:|---:|---:|---:|
| autocomplete | terms rows | 3,447,294 | 3,637,914 | +190,620 | +5.5% |
| tier0-info-for-overlay | edge_publications rows | 7,285,007 | 8,239,919 | +954,912 | +13.1% |
| tier0-info-for-overlay | neighbors rows | 1,670,341 | 1,766,267 | +95,926 | +5.7% |
| tier0-info-for-overlay | category_counts rows | 62 | 68 | +6 | +9.7% |
| curie_to_pmids | CURIEs | 1,217,788 | 1,268,342 | +50,554 | +4.2% |
| curie_to_pmids | (CURIE, PMID) pairs | 391,272,012 | 400,779,758 | +9,507,746 | +2.4% |
| curie_ngd | CURIEs | 717,348 | 759,176 | +41,828 | +5.8% |
| curie_ngd | CURIEs with neighbor NGDs | 206,336 | 225,703 | +19,367 | +9.4% |
| curie_ngd | neighbor NGD pairs | 11,094,440 | 13,043,772 | +1,949,332 | +17.6% |
| fda_approved_drugs | drug CURIEs | 4,797 | 4,796 | -1 | -0.0% |

## Release

- Staged on `arax_databases`: `/home/rtxconfig/tier0-20260928` (uploaded and verified)
- Staged on `arax_ncats`: `/translator/data/orangeboard/databases/tier0-20260928` (uploaded and verified)
- Staged on `cicd`: `/mnt/data/orangeboard/databases/tier0-20260928` (uploaded and verified)
- RTX PR: dry run only, branch `autobuild/tier0-20260928` not pushed
