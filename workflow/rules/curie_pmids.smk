PUBMED_DIR = Path(config["pubmed_dir"])

# build_ngd_database.py writes fixed filenames into --output-dir, so it gets
# its own work dir; curie_to_pmids is moved out under its release filename and
# the rest (the conceptname_to_pmids intermediate, which ARAX does not use, and
# the list of unrecognized concept names) is deleted with the work dir.
CURIE_PMIDS_WORK = WORK_DIR / "curie_pmids"


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
        artifact_log(PUBMED_MANIFEST),
    benchmark:
        artifact_benchmark(PUBMED_MANIFEST)
    shell:
        "python {input.script} --dest {params.dest} --manifest {output} > {log} 2>&1"


rule curie_to_pmids:
    input:
        script=SCRIPTS / "curie_pmids" / "build_ngd_database.py",
        extraction=SCRIPTS / "curie_pmids" / "extraction_script.py",
        manifest=PUBMED_MANIFEST,
        babel=BABEL_SQLITE,
        edges=KGX_DIR / "edges.jsonl",
    output:
        CURIE_TO_PMIDS_DB,
    params:
        pubmed_dir=PUBMED_DIR,
        work=CURIE_PMIDS_WORK,
    log:
        artifact_log(CURIE_TO_PMIDS_DB),
    benchmark:
        artifact_benchmark(CURIE_TO_PMIDS_DB)
    threads: workflow.cores
    shell:
        "rm -rf {params.work} && mkdir -p {params.work}"
        " && python {input.script} --full --skip-download"
        " --pubmed-dir {params.pubmed_dir}"
        " --babel-db {input.babel}"
        " --tier0-edges {input.edges}"
        " --output-dir {params.work} > {log} 2>&1"
        " && mv {params.work}/curie_to_pmids.sqlite {output}"
        " && rm -rf {params.work}"
