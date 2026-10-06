# Copy this build's report, counts, logs and test results into the repo's
# build records, after staging and the RTX PR when those steps are enabled.
rule record_build:
    input:
        script=SCRIPTS / "report" / "record_build.py",
        report=BUILD_REPORT,
        stats=ARTIFACT_STATS,
        config_dbs=CONFIG_DBS,
        pubmed_manifest=PUBMED_MANIFEST,
        md5s=[MD5_DIR / f"{artifact.name}.md5" for artifact in RELEASE_ARTIFACTS],
        artifact_tests=ARTIFACT_TEST_RESULTS,
        config_dbs_tests=CONFIG_DBS_TEST_RESULTS,
        staged=STAGE_RESULTS if STAGING.get("enabled") else [],
        rtx_pr=[RTX_PR_RESULT] if RTX_PR.get("enabled") else [],
    output:
        BUILD_RECORD,
    shell:
        "python {input.script} --build-dir {BUILD_DIR}"
        " --record-dir {RECORDS_ROOT}/{VERSION}"
