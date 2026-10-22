# Files moved out of RTX

These files were removed from the RTX repo (branch `clean_up`, based on RTX commit `b2ce180d6`)
because nothing in them runs at query time. They are kept here because they may still be useful.
Paths under `code/` mirror where each file lived in RTX, so its history can be found there with
`git log -- <path>`.

None of these are wired into the Snakemake workflow yet, and several still assume the old RTX
layout (relative `sys.path` appends, paths under `RTX/code/...`), so expect to adapt them before use.

| File | What it does |
| --- | --- |
| `code/ARAX/KnowledgeSources/COHD_local/scripts/generate_COHD_database.sh`, `OMOP_mapping_parallel.py`, `generate_synoym_pkl.py`, `map_curie_to_OMOP.py` | Build the COHD database ARAX still uses (`COHDdatabase_v1.0_KG2.8.0.db`). Written against KG2. |
| `code/ARAX/KnowledgeSources/COHD_local/data/README.md` | Notes on the COHD data files |
| `code/ARAX/ARAXQuery/Infer/scripts/build_transcription_factor_db.py`, `Infer/data/xCRG_data/transcription_factors.json` | Build the xCRG transcription factor data |
| `code/autocomplete/quick_def_creator.py` | Generates `code/autocomplete/data/quick_def.js`, which the RTX web UI loads |
| `code/ARAX/Documentation/document_dsl_commands.py` | Regenerates `code/ARAX/Documentation/DSL_Documentation.md` in RTX from each module's `describe_me()` |
| `code/UI/interactive/wf2js.py` | Regenerates `code/UI/interactive/wfoperations.js` in RTX |
| `code/UI/interactive/dsl2json.py` | Converts ARAXi DSL to JSON operations |
| `code/UI/OpenAPI/python-flask-server/workflow2yaml.py`, `yaml_reformat.py` | OpenAPI spec utilities |
| `code/ARAX/ResponseCache/upload_old_responses_to_s3.py`, `cp_trapi_resp_betw_s3_buckets.py`, `reinitialize_database.py`, `test_response.py` | One-off response cache admin scripts (S3 copy, MySQL reset) |
