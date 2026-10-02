AUTOCOMPLETE_TSV = BUILD_DIR / "autocomplete_names.tsv"


rule autocomplete_extract:
    input:
        script=SCRIPTS / "autocomplete" / "extract_names_ids.py",
        nodes=KGX_DIR / "nodes.jsonl",
    output:
        temp(AUTOCOMPLETE_TSV),
    log:
        LOG_DIR / "autocomplete_extract.log",
    benchmark:
        BENCH_DIR / "autocomplete_extract.tsv"
    shell:
        "python {input.script} {input.nodes} {output} > {log} 2>&1"


rule autocomplete_load:
    input:
        script=SCRIPTS / "autocomplete" / "create_load_db.py",
        tsv=AUTOCOMPLETE_TSV,
    output:
        AUTOCOMPLETE_DB,
    log:
        LOG_DIR / "autocomplete_load.log",
    benchmark:
        BENCH_DIR / "autocomplete_load.tsv"
    shell:
        "python {input.script} -i {input.tsv} -o {output} > {log} 2>&1"
