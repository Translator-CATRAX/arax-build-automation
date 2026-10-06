# Names and IDs are pulled from the graph's nodes into a TSV in WORK_DIR, then
# loaded into the autocomplete database. The TSV is deleted once loaded.
rule autocomplete:
    input:
        extract=SCRIPTS / "autocomplete" / "extract_names_ids.py",
        load=SCRIPTS / "autocomplete" / "create_load_db.py",
        nodes=KGX_DIR / "nodes.jsonl",
    output:
        AUTOCOMPLETE_DB,
    params:
        tsv=WORK_DIR / "autocomplete_names.tsv",
    log:
        artifact_log(AUTOCOMPLETE_DB),
    benchmark:
        artifact_benchmark(AUTOCOMPLETE_DB)
    shell:
        "mkdir -p $(dirname {params.tsv})"
        " && python {input.extract} {input.nodes} {params.tsv} > {log} 2>&1"
        " && python {input.load} -i {params.tsv} -o {output} >> {log} 2>&1"
        " && rm {params.tsv}"
