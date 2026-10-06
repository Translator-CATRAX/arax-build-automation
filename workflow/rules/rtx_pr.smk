# Open a PR to RTX with this build's config_dbs.json, once every test has passed
# and the report is written. See rtx_pr in config.yaml; with push: false this
# is a dry run. Run it on its own with `snakemake rtx_config_pr`.
rule rtx_config_pr:
    input:
        script=SCRIPTS / "arax" / "open_config_pr.py",
        config_dbs=CONFIG_DBS,
        config_dbs_tests=CONFIG_DBS_TEST_RESULTS,
        artifact_tests=ARTIFACT_TEST_RESULTS,
        report=BUILD_REPORT,
        md5s=[MD5_DIR / f"{artifact.name}.md5" for artifact in RELEASE_ARTIFACTS],
    output:
        RTX_PR_RESULT,
    params:
        push="--push" if RTX_PR.get("push") else "",
        repo=RTX_PR.get("repo"),
        remote=RTX_PR.get("remote"),
        base=RTX_PR.get("base", "master"),
        branch_prefix=RTX_PR.get("branch_prefix", "autobuild/"),
        staged_host=RTX_PR.get("staged_host"),
        staged_dir=str(RTX_PR.get("staged_dir", "")).format(version=VERSION),
    log:
        artifact_log(RTX_PR_RESULT),
    shell:
        "python {input.script} --config-dbs {input.config_dbs} --report {input.report}"
        " --md5-dir {MD5_DIR} --version {VERSION} --repo {params.repo}"
        " --remote {params.remote} --base {params.base}"
        " --branch-prefix {params.branch_prefix} --staged-host {params.staged_host}"
        " --staged-dir {params.staged_dir} {params.push}"
        " --result {output} > {log} 2>&1"
