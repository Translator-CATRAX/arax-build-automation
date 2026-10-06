# Write an updated ARAX config_dbs.json that points every database this
# pipeline builds at its new file, once the artifacts have passed their tests.
# Entries for databases built elsewhere (explainable_dtd_db, cohd_database) are
# carried over unchanged from RTX's config_dbs.json.
rule arax_config_dbs:
    input:
        script=SCRIPTS / "arax" / "update_config_dbs.py",
        template=config["rtx_config_dbs"],
        artifacts=RELEASE_ARTIFACTS,
        tests=ARTIFACT_TEST_RESULTS,
    output:
        CONFIG_DBS,
    params:
        db_dir=f"{config['arax_db_root']}/{TIER0}",
        overlay=OVERLAY_DB.name,
        autocomplete=AUTOCOMPLETE_DB.name,
        curie_to_pmids=CURIE_TO_PMIDS_DB.name,
        curie_ngd=CURIE_NGD_DB.name,
        fda=FDA_DRUGS_PICKLE.name,
    log:
        artifact_log(CONFIG_DBS),
    shell:
        "python {input.script} --template {input.template} --output {output}"
        " --db-dir {params.db_dir}"
        " --set tier0_sqlite={params.overlay}"
        " --set kg2c_sqlite={params.overlay}"
        " --set autocomplete={params.autocomplete}"
        " --set curie_to_pmids={params.curie_to_pmids}"
        " --set curie_ngd={params.curie_ngd}"
        " --set fda_approved_drugs={params.fda} > {log} 2>&1"
