# Upload the tested artifacts to one staging target. See staging in config.yaml;
# with upload: false this is a dry run. Run all targets on their own with
# `snakemake stage`, or one with `snakemake <build dir>/staged/<target>.json`.
rule stage_target:
    input:
        script=SCRIPTS / "arax" / "stage_databases.py",
        artifacts=RELEASE_ARTIFACTS,
        md5s=[MD5_DIR / f"{artifact.name}.md5" for artifact in RELEASE_ARTIFACTS],
        artifact_tests=ARTIFACT_TEST_RESULTS,
        config_dbs_tests=CONFIG_DBS_TEST_RESULTS,
    output:
        BUILD_DIR / "staged" / "{target}.json",
    wildcard_constraints:
        target="|".join(STAGING_TARGETS),
    params:
        upload="--upload" if STAGING.get("upload") else "",
        host=lambda wildcards: STAGING_TARGETS[wildcards.target]["host"],
        root=lambda wildcards: STAGING_TARGETS[wildcards.target]["root"],
        dir_mode=lambda wildcards: STAGING_TARGETS[wildcards.target].get("dir_mode", "775"),
        sudo=lambda wildcards: "--sudo" if STAGING_TARGETS[wildcards.target].get("sudo") else "",
        read_check=lambda wildcards: shlex.quote(STAGING_TARGETS[wildcards.target].get("read_check", "")),
    log:
        LOG_DIR / "staged_{target}.log",
    shell:
        "python {input.script} --build-dir {BUILD_DIR} --md5-dir {MD5_DIR}"
        " --release {TIER0} --host {params.host} --root {params.root}"
        " --dir-mode {params.dir_mode} {params.sudo} --read-check {params.read_check}"
        " {params.upload}"
        " --result {output} > {log} 2>&1"


rule stage:
    input:
        STAGE_RESULTS,
