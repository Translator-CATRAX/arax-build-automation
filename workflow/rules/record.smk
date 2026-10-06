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


# Commit and push the build record to the autobuild repo. It requires the
# staging and RTX PR results, so asking for it runs those steps too; it stops
# unless they ran for real. See build_record in config.yaml.
rule push_build_record:
    input:
        script=SCRIPTS / "report" / "push_build_record.py",
        record=BUILD_RECORD,
        staged=STAGE_RESULTS,
        rtx_pr=RTX_PR_RESULT,
    output:
        RECORD_PUSH_RESULT,
    params:
        repo_dir=Path(workflow.basedir).parent,
        remote=RECORD_PUSH.get("remote", "origin"),
        branch=RECORD_PUSH.get("branch", "master"),
        push="--push" if RECORD_PUSH.get("push") else "",
    log:
        LOG_DIR / "push_build_record.log",
    shell:
        "python {input.script} --repo-dir {params.repo_dir} --version {VERSION}"
        " --staged {input.staged} --rtx-pr {input.rtx_pr}"
        " --remote {params.remote} --branch {params.branch} {params.push}"
        " --result {output} > {log} 2>&1"
