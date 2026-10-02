# Download inputs from the webhost. These run only when the chosen version's
# local files are missing (see the version resolution in the Snakefile).
#
# The fetch script is deliberately a param, not an input: as an input, editing
# it would make Snakemake treat the downloaded data as stale and re-download
# it. Outputs are protected so a stray rerun fails instead of deleting data.

rule fetch_tier0:
    output:
        nodes=protected(KGX_DIR / "nodes.jsonl"),
        edges=protected(KGX_DIR / "edges.jsonl"),
        metadata=protected(GRAPH_METADATA),
    params:
        script=SCRIPTS / "sources" / "webhost.py",
        host=WEBHOST,
        remote=config["webhost_tier0_root"],
        version=VERSION,
        dest=KGX_DIR,
    log:
        LOG_DIR / "fetch_tier0.log",
    shell:
        "python {params.script} tier0 --host {params.host} --remote {params.remote}"
        " --version {params.version} --dest {params.dest} > {log} 2>&1"


rule fetch_drugbank:
    output:
        protected(DRUGBANK_XML),
    params:
        script=SCRIPTS / "sources" / "webhost.py",
        host=WEBHOST,
        remote=config["webhost_drugbank"],
        version=DRUGBANK_VERSION,
    log:
        LOG_DIR / "fetch_drugbank.log",
    shell:
        "python {params.script} drugbank --host {params.host} --remote {params.remote}"
        " --version {params.version} --dest {output} > {log} 2>&1"
