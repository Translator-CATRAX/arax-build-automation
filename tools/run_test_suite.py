#!/usr/bin/env python3
"""Run a Translator test suite against ARAX with a Tier0 build's databases.

By default this stands up ARAX on this machine from the RTX checkout's
current branch (the rtx_config_dbs checkout in config/config.yaml), pointed at
the databases in {output_root}/YYYYMMDD/, runs the suite from a checkout of
NCATSTranslator/Tests against it, and stops the server again:

    python tools/run_test_suite.py --tier0 20260928

The checkout's code/config_dbs.json decides which database files ARAX opens,
so check out the branch that uses the build first; the run stops if
config_dbs.json points at a different Tier0 release. Each database is
symlinked into the checkout where ARAX looks for it (gitignored paths only).
flask_config.json and maturity_override.txt are written for the run and put
back as they were afterwards.

With --url it runs the suite against an ARAX that is already running instead
(a baseline, e.g. https://arax.ci.transltr.io/api/arax/v1.4), and sets up
nothing locally.

Queries go to ARAX only. Each asset is graded with ARS_Test_Runner's
pass_fail_analysis, the same as the Test Harness. Results land in
{output_root}/YYYYMMDD/test_suites/<suite>-<time>/: results.csv,
results.json and summary.md.

Two venvs are kept in {output_root}/.cache/: arax-venv (RTX's
requirements.txt, refreshed every run) for the server, and suite-venv
(ARS_Test_Runner) for the queries and grading.
"""

import argparse
import asyncio
import csv
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ARS_TEST_RUNNER = "ARS-Test-Runner==0.2.6"
SERVER_START_TIMEOUT = 600

# Where ARAX_database_manager.py expects each config_dbs.json database, relative to RTX/code.
LOCAL_DIRS = {
    "tier0_sqlite": "ARAX/KnowledgeSources/KG2c",
    "kg2c_sqlite": "ARAX/KnowledgeSources/KG2c",
    "curie_to_pmids": "ARAX/KnowledgeSources/NormalizedGoogleDistance",
    "curie_ngd": "ARAX/KnowledgeSources/NormalizedGoogleDistance",
    "fda_approved_drugs": "ARAX/KnowledgeSources",
    "autocomplete": "autocomplete",
    "explainable_dtd_db": "ARAX/KnowledgeSources/Prediction",
    "cohd_database": "ARAX/KnowledgeSources/COHD_local/data",
}

FLASK_CONFIG = {
    "check_databases": False,
    "run_background_tasker": False,
    "force_disable_telemetry": True,
    "query_fork_mode": False,
}

TREATS_QUERY = {
    "message": {
        "query_graph": {
            "nodes": {
                "ON": {"ids": [], "categories": ["biolink:Disease"]},
                "SN": {"categories": ["biolink:ChemicalEntity"]},
            },
            "edges": {
                "e01": {"object": "ON", "subject": "SN", "predicates": ["biolink:treats"],
                        "knowledge_type": "inferred"},
            },
        }
    }
}

AFFECTS_QUERY = {
    "message": {
        "query_graph": {
            "nodes": {
                "ON": {"ids": [], "categories": ["biolink:Gene"]},
                "SN": {"ids": [], "categories": ["biolink:ChemicalEntity"]},
            },
            "edges": {
                "t_edge": {
                    "object": "ON", "subject": "SN", "predicates": ["biolink:affects"],
                    "knowledge_type": "inferred",
                    "qualifier_constraints": [{"qualifier_set": [
                        {"qualifier_type_id": "biolink:object_aspect_qualifier", "qualifier_value": ""},
                        {"qualifier_type_id": "biolink:object_direction_qualifier", "qualifier_value": ""},
                    ]}],
                }
            },
        }
    }
}


class Stop(Exception):
    """A precondition failed."""


def run(*args, **kwargs) -> subprocess.CompletedProcess:
    result = subprocess.run([str(a) for a in args], text=True, capture_output=True, **kwargs)
    if result.returncode != 0:
        raise Stop(f"`{' '.join(map(str, args))}` failed: {(result.stderr or result.stdout).strip()[-2000:]}")
    return result


def load_config() -> dict:
    import yaml
    return yaml.safe_load((REPO / "config" / "config.yaml").read_text())


# ---------------------------------------------------------------- local setup

def check_config_dbs(rtx: Path, tier0: str) -> dict:
    """The checkout's database_downloads, after checking every Tier0 path is this release."""
    downloads = json.loads((rtx / "code" / "config_dbs.json").read_text())["database_downloads"]
    wrong = {k: p for k, p in downloads.items() if "/tier0-" in p and f"/{tier0}/" not in p}
    if wrong:
        raise Stop(f"{rtx}/code/config_dbs.json points these at another release than {tier0}; "
                   "check out the branch that uses this build:\n"
                   + "\n".join(f"  {k}: {p}" for k, p in wrong.items()))
    return downloads


def link_databases(rtx: Path, downloads: dict, build_dir: Path, output_root: Path) -> None:
    """Symlink each database into the checkout where ARAX looks for it."""
    missing = []
    for key, remote_path in downloads.items():
        if key not in LOCAL_DIRS:
            continue
        name = remote_path.rsplit("/", 1)[-1]
        dest = rtx / "code" / LOCAL_DIRS[key] / name
        if dest.exists():
            continue
        candidates = [build_dir / name, *sorted(output_root.glob(f"*/{name}"))]
        source = next((c for c in candidates if c.is_file()), None)
        if source is None:
            missing.append(f"  {key}: {name}")
            continue
        if dest.is_symlink():  # broken link
            dest.unlink()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.symlink_to(source)
        print(f"Linked {dest.relative_to(rtx)} -> {source}")
    if missing:
        raise Stop(f"No local copy under {output_root} of:\n" + "\n".join(missing))


def ensure_venv(path: Path, *requirements: str) -> Path:
    """Create the venv if needed and install requirements into it; return its python."""
    python = path / "bin" / "python"
    if not python.exists():
        print(f"Creating {path}")
        run("uv", "venv", "--python", "3.12", path)
    print(f"Installing {' '.join(requirements)} into {path}")
    run("uv", "pip", "install", "--python", python, *requirements)
    return python


def port_in_use(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_for_server(base: str, process: subprocess.Popen, log: Path) -> None:
    deadline = time.time() + SERVER_START_TIMEOUT
    while time.time() < deadline:
        if process.poll() is not None:
            raise Stop(f"ARAX exited with code {process.returncode}; last lines of {log}:\n"
                       + "\n".join(log.read_text().splitlines()[-30:]))
        try:
            with urllib.request.urlopen(f"{base}/status", timeout=5) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        time.sleep(5)
    raise Stop(f"ARAX did not answer {base}/status within {SERVER_START_TIMEOUT}s; see {log}")


class TemporaryFile:
    """Write a file for the run, and put back what was there before afterwards."""

    def __init__(self, path: Path, content: str):
        self.path, self.content = path, content
        self.original = path.read_text() if path.exists() else None

    def __enter__(self):
        self.path.write_text(self.content)
        return self

    def __exit__(self, *exc):
        if self.original is None:
            self.path.unlink(missing_ok=True)
        else:
            self.path.write_text(self.original)


def stop_server(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(30)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def git_describe(path: Path) -> dict:
    def git(*args):
        return subprocess.run(["git", "-C", str(path), *args], text=True,
                              capture_output=True).stdout.strip()
    return {"path": str(path), "branch": git("branch", "--show-current"),
            "commit": git("rev-parse", "--short", "HEAD"),
            "uncommitted_changes": bool(git("status", "--porcelain", "--untracked-files=no"))}


# -------------------------------------------------------------- query + grade
# Runs inside suite-venv (--grade), where ARS_Test_Runner is installed.

def to_trapi2_query(query):
    """Rewrite a generate_message() query in TRAPI 2.0 form, asking the same question:
    qualifier_constraints become constraints.qualifiers maps, and empty ids lists are dropped."""
    for qnode in query["message"]["query_graph"]["nodes"].values():
        if "ids" in qnode and not qnode["ids"]:
            del qnode["ids"]
    for qedge in query["message"]["query_graph"]["edges"].values():
        qualifier_constraints = qedge.pop("qualifier_constraints", None)
        if qualifier_constraints:
            qedge["constraints"] = {"qualifiers": [
                {q["qualifier_type_id"]: q["qualifier_value"] for q in qs["qualifier_set"]}
                for qs in qualifier_constraints]}
    return query


def to_trapi1_node_bindings(results):
    """ARS_Test_Runner's pass_fail_analysis reads TRAPI 1.x node bindings (a list of {"id": ...});
    give it TRAPI 2.0 results in that shape. Only the binding layout changes; the ids are the same."""
    converted = []
    for result in results:
        result = dict(result)
        result["node_bindings"] = {
            qnode_key: [{"id": node_id} for node_id in binding["ids"]] if isinstance(binding, dict) else binding
            for qnode_key, binding in (result.get("node_bindings") or {}).items()}
        converted.append(result)
    return converted


def generate_message(predicate, input_curie, aspect, direction, input_category):
    """The query for a test case, as in the Test Harness's run_test_suite notebook."""
    query = json.loads(json.dumps(TREATS_QUERY if predicate == "treats" else AFFECTS_QUERY))
    nodes = query["message"]["query_graph"]["nodes"]
    edges = query["message"]["query_graph"]["edges"]
    if aspect == "" and direction == "" and input_category == "biolink:Disease":
        for node in nodes.values():
            if "ids" in node:
                node["ids"].append(input_curie)
    elif input_category in ("biolink:Gene", "biolink:ChemicalEntity"):
        filled, emptied = ("ON", "SN") if input_category == "biolink:Gene" else ("SN", "ON")
        nodes[filled]["ids"].append(input_curie)
        del nodes[emptied]["ids"]
        qualifier_set = edges["t_edge"]["qualifier_constraints"][0]["qualifier_set"]
        qualifier_set[0]["qualifier_value"] = aspect
        qualifier_set[1]["qualifier_value"] = direction
    else:
        return None
    return query


def grade(base: str, suite_path: Path, out_dir: Path, metadata_path: Path) -> None:
    import requests
    from ARS_Test_Runner.semantic_test import pass_fail_analysis

    suite = json.loads(suite_path.read_text())
    metadata = json.loads(metadata_path.read_text())
    preferred_ids = {}

    def preferred_id(curie):
        if curie not in preferred_ids:
            try:
                response = requests.post(f"{base}/entity", timeout=120,
                                         json={"terms": [curie], "max_synonyms": 10, "format": "slim"})
                preferred_ids[curie] = response.json()[curie]["id"]["identifier"]
            except Exception:
                preferred_ids[curie] = curie
        return preferred_ids[curie]

    queries, assets = [], []
    trapi2 = "/v2.0" in base
    cases = suite["test_cases"]
    for number, (case_id, case) in enumerate(cases.items(), 1):
        predicate = case["test_case_predicate_name"]
        input_curie = case["test_case_input_id"]
        input_category = case["input_category"] or case["test_assets"][0]["input_category"]
        qualifiers = {q["parameter"]: q["value"] for q in case.get("qualifiers") or []}
        query = generate_message(predicate, input_curie,
                                 qualifiers.get("biolink_object_aspect_qualifier", ""),
                                 qualifiers.get("biolink_object_direction_qualifier", ""),
                                 input_category)
        if query is not None and trapi2:
            query = to_trapi2_query(query)
        results, response_id, http = None, None, None
        started = time.time()
        if query is None:
            http = f"unsupported input category {input_category}"
        else:
            try:
                response = requests.post(f"{base}/query", json=query, timeout=1800)
                http = response.status_code
                if http == 200:
                    body = response.json()
                    results = body["message"].get("results") or []
                    response_id = body.get("id")
            except Exception as e:
                http = f"{type(e).__name__}: {e}"
        seconds = round(time.time() - started, 1)
        print(f"[{number}/{len(cases)}] {case_id} {predicate} {input_curie}: HTTP {http}, "
              f"{'-' if results is None else len(results)} results, {seconds}s", flush=True)
        queries.append({"test_case": case_id, "predicate": predicate, "input": input_curie,
                        "http": http, "results": None if results is None else len(results),
                        "seconds": seconds, "response_id": response_id})

        for asset in case["test_assets"]:
            if results is None:
                status = "ERROR"
            elif not results:
                status = "No results"
            else:
                try:
                    graded_results = to_trapi1_node_bindings(results) if trapi2 else results
                    report = asyncio.run(pass_fail_analysis({"arax": {}}, "arax", graded_results,
                                                            preferred_id(asset["output_id"]),
                                                            asset["expected_output"]))
                    status = report["arax"]["status"]
                except Exception:
                    status = "ERROR"
            assets.append({"test_case": case_id, "test_asset": asset["id"], "name": asset["name"],
                           "expected_output": asset["expected_output"],
                           "output_id": asset["output_id"], "status": status,
                           "response_id": response_id})

    with (out_dir / "results.csv").open("w", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=list(assets[0]))
        writer.writeheader()
        writer.writerows(assets)
    (out_dir / "results.json").write_text(json.dumps(
        {**metadata, "queries": queries, "assets": assets}, indent=1) + "\n")

    totals = {}
    for asset in assets:
        passed, total = totals.get(asset["expected_output"], (0, 0))
        totals[asset["expected_output"]] = (passed + (asset["status"] == "PASSED"), total + 1)
    passed = sum(p for p, _ in totals.values())
    lines = [f"# {suite_path.name} against {base}", ""]
    lines += [f"- {key}: {value}" for key, value in metadata.items() if key != "rtx"]
    if "rtx" in metadata:
        rtx = metadata["rtx"]
        lines.append(f"- RTX: {rtx['branch']} @ {rtx['commit']}"
                     + (" (with uncommitted changes)" if rtx["uncommitted_changes"] else ""))
    lines += ["", "| expected | passed | total |", "|---|---|---|"]
    lines += [f"| {kind} | {p} | {t} |" for kind, (p, t) in sorted(totals.items())]
    lines += [f"| **all** | **{passed}** | **{len(assets)}** ({100 * passed / len(assets):.1f}%) |", ""]
    errors = [q for q in queries if q["http"] != 200]
    timed = [q["seconds"] for q in queries if q["http"] == 200]
    if timed:
        lines.append(f"{len(timed)} of {len(queries)} queries answered; median "
                     f"{sorted(timed)[len(timed) // 2]}s, slowest {max(timed)}s.")
    if errors:
        lines += ["", "Queries that failed:", ""]
        lines += [f"- {q['test_case']} {q['predicate']} {q['input']}: {q['http']}" for q in errors]
    failed = [a for a in assets if a["status"] != "PASSED"]
    if failed:
        lines += ["", "| test case | expected | answer | status |", "|---|---|---|---|"]
        lines += [f"| {a['test_case']} | {a['expected_output']} | {a['name'].split(': ', 1)[-1]} "
                  f"| {a['status']} |" for a in failed]
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n")

    print()
    for kind, (p, t) in sorted(totals.items()):
        print(f"{kind:18s} {p:4d} / {t}")
    print(f"{'all':18s} {passed:4d} / {len(assets)} ({100 * passed / len(assets):.1f}%)")
    print(f"\nResults in {out_dir}")


# ----------------------------------------------------------------------- main

def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--grade":
        grade(sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4]), Path(sys.argv[5]))
        return

    config = load_config()
    output_root = Path(config["output_root"])
    default_rtx = Path(config["rtx_config_dbs"]).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tier0", help="Build version, YYYYMMDD (or tier0-YYYYMMDD).")
    parser.add_argument("--suite", default="sprint_6_tests.json",
                        help="Suite file in the Tests checkout's test_suites/ (default: %(default)s).")
    parser.add_argument("--url", help="Test this running ARAX instead of a local one, e.g. "
                                      "https://arax.ci.transltr.io/api/arax/v1.4")
    parser.add_argument("--rtx", type=Path, default=default_rtx,
                        help="RTX checkout to run (default: %(default)s).")
    parser.add_argument("--tests-dir", type=Path, default=default_rtx.parent / "Tests",
                        help="Checkout of NCATSTranslator/Tests (default: %(default)s).")
    parser.add_argument("--no-pull", action="store_true", help="Do not git pull the Tests checkout.")
    parser.add_argument("--port", type=int, default=5001, help="Local ARAX port (default: %(default)s).")
    parser.add_argument("--maturity", default="staging",
                        help="ARAX maturity for the local server, as on CI (default: %(default)s).")
    parser.add_argument("--keep-server", action="store_true",
                        help="Leave the local ARAX running afterwards.")
    args = parser.parse_args()
    if not args.tier0 and not args.url:
        parser.error("pass --tier0 YYYYMMDD, or --url to test a running ARAX")

    version = args.tier0.removeprefix("tier0-") if args.tier0 else None
    tier0 = f"tier0-{version}" if version else None
    build_dir = output_root / version if version else None
    cache = output_root / ".cache"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = (build_dir or output_root) / "test_suites" / f"{Path(args.suite).stem}-{stamp}"
    server = None
    temporary_files = []

    try:
        if build_dir and not build_dir.is_dir():
            raise Stop(f"No build directory {build_dir}")
        if not (args.tests_dir / ".git").is_dir():
            run("git", "clone", "https://github.com/NCATSTranslator/Tests.git", args.tests_dir)
        elif not args.no_pull:
            run("git", "-C", args.tests_dir, "pull", "--ff-only")
        suite_path = args.tests_dir / "test_suites" / args.suite
        if not suite_path.is_file():
            raise Stop(f"No suite {suite_path}")
        suite_python = ensure_venv(cache / "suite-venv", ARS_TEST_RUNNER, "requests")

        metadata = {"suite": args.suite, "tests_commit": git_describe(args.tests_dir)["commit"],
                    "started": datetime.now().isoformat(timespec="seconds")}
        if args.url:
            base = args.url.rstrip("/")
            if tier0:
                metadata["tier0"] = tier0
        else:
            base = f"http://localhost:{args.port}/api/arax/v1.4"
            metadata |= {"tier0": tier0, "rtx": git_describe(args.rtx)}
            downloads = check_config_dbs(args.rtx, tier0)
            link_databases(args.rtx, downloads, build_dir, output_root)
            arax_python = ensure_venv(cache / "arax-venv", "-r", str(args.rtx / "requirements.txt"))
            if port_in_use(args.port):
                raise Stop(f"Something is already listening on port {args.port}; pass --port")

            server_dir = args.rtx / "code" / "UI" / "OpenAPI" / "python-flask-server"
            for path, content in [
                (server_dir / "openapi_server" / "flask_config.json",
                 json.dumps({"port": args.port, **FLASK_CONFIG}, indent=4) + "\n"),
                (args.rtx / "code" / "maturity_override.txt", args.maturity + "\n"),
            ]:
                temporary_files.append(TemporaryFile(path, content).__enter__())

            print("Refreshing ARAX's KP info cache")
            run(arax_python, "kp_info_cacher.py", cwd=args.rtx / "code" / "ARAX" / "ARAXQuery" / "Expand")

            out_dir.mkdir(parents=True)
            server_log = out_dir / "arax_server.log"
            print(f"Starting ARAX on port {args.port} (log: {server_log})")
            with server_log.open("w") as log:
                server = subprocess.Popen([str(arax_python), "-m", "openapi_server"], cwd=server_dir,
                                          stdout=log, stderr=subprocess.STDOUT,
                                          stdin=subprocess.DEVNULL, start_new_session=True)
            wait_for_server(base, server, server_log)

        out_dir.mkdir(parents=True, exist_ok=True)
        metadata["base_url"] = base
        metadata_path = out_dir / "metadata.json"
        metadata_path.write_text(json.dumps(metadata, indent=1) + "\n")
        print(f"Running {args.suite} against {base}")
        # ARS_Test_Runner logs to test_ars.log in the working directory.
        result = subprocess.run([str(suite_python), "-I", str(Path(__file__).resolve()), "--grade", base,
                                 str(suite_path), str(out_dir), str(metadata_path)], cwd=out_dir)
        if result.returncode != 0:
            raise Stop(f"The suite run failed (exit {result.returncode})")
    except Stop as stop:
        print(f"STOPPED: {stop}", file=sys.stderr)
        raise SystemExit(1)
    finally:
        if server is not None:
            if args.keep_server and server.poll() is None:
                print(f"ARAX left running on port {args.port} (pid {server.pid})")
            else:
                stop_server(server)
        if not (args.keep_server and server is not None):
            for temporary in reversed(temporary_files):
                temporary.__exit__(None, None, None)


if __name__ == "__main__":
    main()
