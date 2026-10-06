# One .md5 per release artifact, in `md5sum` format with the bare filename so
# `md5sum -c` works from the staging directory. Written only after the artifact
# tests pass: a release is staged by its md5s, so a build that fails its tests
# has none.
rule md5:
    input:
        artifact=BUILD_DIR / "{artifact}",
        tests=ARTIFACT_TEST_RESULTS,
    output:
        MD5_DIR / "{artifact}.md5",
    params:
        build_dir=BUILD_DIR,
    wildcard_constraints:
        artifact=r"[^/]+",
    shell:
        "cd {params.build_dir} && md5sum {wildcards.artifact} > {output}"
