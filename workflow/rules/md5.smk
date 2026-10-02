# One .md5 per release artifact, in `md5sum` format with the bare filename so
# `md5sum -c` works from the staging directory.
rule md5:
    input:
        BUILD_DIR / "{artifact}",
    output:
        MD5_DIR / "{artifact}.md5",
    params:
        build_dir=BUILD_DIR,
    wildcard_constraints:
        artifact=r"[^/]+",
    shell:
        "cd {params.build_dir} && md5sum {wildcards.artifact} > {output}"
