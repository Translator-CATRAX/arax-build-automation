# Download DrugBank from the webhost. This runs only when the chosen version's
# local file is missing (see the version resolution in the Snakefile).
#
# The fetch script is deliberately a param, not an input: as an input, editing
# it would make Snakemake treat the downloaded data as stale and re-download
# it. The output is protected so a stray rerun fails instead of deleting data.

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
        "python {params.script} --host {params.host} --remote {params.remote}"
        " --version {params.version} --dest {output} > {log} 2>&1"


# Download the Babel sqlite from its stitch-proj release. This runs only when
# no local sqlite matches the graph's babelVersion (see the Snakefile).
if BABEL_RELEASE:
    rule fetch_babel:
        output:
            protected(BABEL_SQLITE),
        params:
            script=SCRIPTS / "sources" / "babel.py",
            url=BABEL_RELEASE.url,
            md5=BABEL_RELEASE.md5,
        log:
            LOG_DIR / "fetch_babel.log",
        shell:
            "python {params.script} --url {params.url} --md5 {params.md5}"
            " --dest {output} > {log} 2>&1"
