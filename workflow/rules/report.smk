TESTS_DIR = Path(workflow.basedir).parent / "tests"
CONFIG_DBS_TESTS = TESTS_DIR / "test_config_dbs.py"
ARTIFACT_TESTS = [t for t in sorted(TESTS_DIR.glob("*.py")) if t != CONFIG_DBS_TESTS]


# Interrogate the finished artifacts with the pytest suite in tests/. The md5s
# and config_dbs.json are written only after these pass, so a build that fails
# them has nothing to stage and no report.
rule test_artifacts:
    input:
        tests=ARTIFACT_TESTS,
        artifacts=RELEASE_ARTIFACTS,
        nodes=KGX_DIR / "nodes.jsonl",
        edges=KGX_DIR / "edges.jsonl",
        babel=BABEL_SQLITE,
    output:
        ARTIFACT_TEST_RESULTS,
    params:
        tests_dir=TESTS_DIR,
        config_dbs_tests=CONFIG_DBS_TESTS,
    log:
        LOG_DIR / "test_artifacts.log",
    shell:
        "python -m pytest {params.tests_dir} -p no:cacheprovider"
        " --ignore {params.config_dbs_tests} --build-dir {BUILD_DIR}"
        " --graph-dir {KGX_DIR} --babel {input.babel}"
        " --biolink-cache-dir {BIOLINK_CACHE_DIR}"
        " --junitxml {output} > {log} 2>&1"


# Check the config_dbs.json written from the verified artifacts.
rule test_config_dbs:
    input:
        tests=[TESTS_DIR / "conftest.py", CONFIG_DBS_TESTS],
        config_dbs=CONFIG_DBS,
        md5s=[MD5_DIR / f"{artifact.name}.md5" for artifact in RELEASE_ARTIFACTS],
        rtx_config_dbs=config["rtx_config_dbs"],
    output:
        CONFIG_DBS_TEST_RESULTS,
    params:
        config_dbs_tests=CONFIG_DBS_TESTS,
    log:
        LOG_DIR / "test_config_dbs.log",
    shell:
        "python -m pytest {params.config_dbs_tests} -p no:cacheprovider"
        " --build-dir {BUILD_DIR} --rtx-config-dbs {input.rtx_config_dbs}"
        " --junitxml {output} > {log} 2>&1"


rule report:
    input:
        script=SCRIPTS / "report" / "build_report.py",
        stats_script=SCRIPTS / "report" / "artifact_stats.py",
        artifacts=RELEASE_ARTIFACTS,
        md5s=[MD5_DIR / f"{artifact.name}.md5" for artifact in RELEASE_ARTIFACTS],
        config_dbs=CONFIG_DBS,
        artifact_tests=ARTIFACT_TEST_RESULTS,
        config_dbs_tests=CONFIG_DBS_TEST_RESULTS,
        pubmed_manifest=PUBMED_MANIFEST,
        drugbank=DRUGBANK_XML,
        babel=BABEL_SQLITE,
    output:
        report=BUILD_REPORT,
        stats=ARTIFACT_STATS,
    log:
        artifact_log(BUILD_REPORT),
    shell:
        "python {input.script} --build-dir {BUILD_DIR} --records-dir {RECORDS_ROOT}"
        f" --artifact autocomplete={AUTOCOMPLETE_DB}"
        f" --artifact tier0_info_for_overlay={OVERLAY_DB}"
        f" --artifact curie_to_pmids={CURIE_TO_PMIDS_DB}"
        f" --artifact curie_ngd={CURIE_NGD_DB}"
        f" --artifact fda_approved_drugs={FDA_DRUGS_PICKLE}"
        " --config-dbs {input.config_dbs} --test-results {input.artifact_tests}"
        " --test-results {input.config_dbs_tests} --graph-dir {KGX_DIR}"
        " --pubmed-manifest {input.pubmed_manifest} --drugbank {input.drugbank}"
        " --babel {input.babel} --biolink-version {BIOLINK_VERSION}"
        " --stats-output {output.stats} --output {output.report}"
        " > {log} 2>&1"
