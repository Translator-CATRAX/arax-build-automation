# Rebuilt whenever the DrugBank version changes, since a new release lands at a
# new versioned path (see fetch_drugbank).
rule fda_drugs:
    input:
        script=SCRIPTS / "fda_drugs" / "generate_fda_pickle.py",
        drugbank=DRUGBANK_XML,
        babel=BABEL_SQLITE,
    output:
        FDA_DRUGS_PICKLE,
    log:
        artifact_log(FDA_DRUGS_PICKLE),
    benchmark:
        artifact_benchmark(FDA_DRUGS_PICKLE)
    shell:
        "python {input.script}"
        " --drugbank_xml {input.drugbank}"
        " --babel_db {input.babel}"
        " --output_pickle {output} > {log} 2>&1"
