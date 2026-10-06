rule overlay:
    input:
        script=SCRIPTS / "overlay" / "generate_sqlite.py",
        nodes=KGX_DIR / "nodes.jsonl",
        edges=KGX_DIR / "edges.jsonl",
        metadata=GRAPH_METADATA,
    output:
        OVERLAY_DB,
    params:
        output_dir=BUILD_DIR,
        version=VERSION,
        biolink_cache_dir=BIOLINK_CACHE_DIR,
    log:
        artifact_log(OVERLAY_DB),
    benchmark:
        artifact_benchmark(OVERLAY_DB)
    shell:
        "python {input.script}"
        " --nodes {input.nodes}"
        " --edges {input.edges}"
        " --output-dir {params.output_dir}"
        " --tier0-build-date {params.version}"
        " --graph-metadata {input.metadata}"
        " --biolink-cache-dir {params.biolink_cache_dir} > {log} 2>&1"
