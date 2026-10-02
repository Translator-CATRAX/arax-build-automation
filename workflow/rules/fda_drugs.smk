# Rebuilt whenever the DrugBank version changes, since a new release lands at a
# new versioned path (see fetch_drugbank).
rule fda_drugs:
    input:
        script=SCRIPTS / "fda_drugs" / "generate_fda_pickle.py",
        drugbank=DRUGBANK_XML,
        babel=config["babel_sqlite"],
    output:
        FDA_DRUGS_PICKLE,
    log:
        LOG_DIR / "fda_drugs.log",
    benchmark:
        BENCH_DIR / "fda_drugs.tsv"
    shell:
        "python {input.script}"
        " --drugbank_xml {input.drugbank}"
        " --babel_db {input.babel}"
        " --output_pickle {output} > {log} 2>&1"
