# Precomputed NGD between each CURIE and its graph neighbors, from the PMID
# lists in curie_to_pmids. Rebuilt whenever curie_to_pmids or the graph changes.
rule curie_ngd:
    input:
        script=SCRIPTS / "curie_ngd" / "build_curie_ngd.py",
        curie_to_pmids=CURIE_TO_PMIDS_DB,
        nodes=KGX_DIR / "nodes.jsonl",
        edges=KGX_DIR / "edges.jsonl",
    output:
        CURIE_NGD_DB,
    log:
        artifact_log(CURIE_NGD_DB),
    benchmark:
        artifact_benchmark(CURIE_NGD_DB)
    threads: workflow.cores
    shell:
        "python {input.script}"
        " --curie-to-pmids {input.curie_to_pmids}"
        " --nodes {input.nodes}"
        " --edges {input.edges}"
        " --output {output}"
        " --workers {threads} > {log} 2>&1"
