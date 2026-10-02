PUBMED_DIR = Path(config["pubmed_dir"])

# build_ngd_database.py writes fixed filenames into --output-dir, so it gets
# its own work dir and the results are renamed to the release filenames.
CURIE_PMIDS_WORK = BUILD_DIR / "curie_pmids_work"


# Creates the mirror on the first run and refreshes it once per build after
# that: downloads new or changed files, verifies them against NCBI's md5s, and
# deletes files no longer on the server. The mirror itself lives outside
# BUILD_DIR so it is shared across builds; the manifest records the snapshot
# this build used.
rule pubmed_mirror:
    input:
        script=SCRIPTS / "curie_pmids" / "mirror_pubmed.py",
    output:
        PUBMED_MANIFEST,
    params:
        dest=PUBMED_DIR,
    log:
        LOG_DIR / "pubmed_mirror.log",
    benchmark:
        BENCH_DIR / "pubmed_mirror.tsv"
    shell:
        "python {input.script} --dest {params.dest} --manifest {output} > {log} 2>&1"


rule curie_to_pmids:
    input:
        script=SCRIPTS / "curie_pmids" / "build_ngd_database.py",
        extraction=SCRIPTS / "curie_pmids" / "extraction_script.py",
        manifest=PUBMED_MANIFEST,
        babel=config["babel_sqlite"],
        edges=KGX_DIR / "edges.jsonl",
    output:
        curie=CURIE_TO_PMIDS_DB,
        conceptname=CONCEPTNAME_TO_PMIDS_DB,
    params:
        pubmed_dir=PUBMED_DIR,
        work=CURIE_PMIDS_WORK,
    log:
        LOG_DIR / "curie_to_pmids.log",
    benchmark:
        BENCH_DIR / "curie_to_pmids.tsv"
    threads: workflow.cores
    shell:
        "mkdir -p {params.work}"
        " && python {input.script} --full --skip-download"
        " --pubmed-dir {params.pubmed_dir}"
        " --babel-db {input.babel}"
        " --tier0-edges {input.edges}"
        " --output-dir {params.work} > {log} 2>&1"
        " && mv {params.work}/curie_to_pmids.sqlite {output.curie}"
        " && mv {params.work}/conceptname_to_pmids.sqlite {output.conceptname}"
