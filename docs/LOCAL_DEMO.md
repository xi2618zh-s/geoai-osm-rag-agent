# Run a school analysis locally

## Install and start

Use Python 3.10 or later. The school path needs Flask, Pydantic, Shapely and pyproj; it avoids the embedding/PBF import tree. This dependency list was installed in a fresh `.venv-demo` and used for actual execution during acceptance.

```powershell
git clone https://github.com/xi2618zh-s/geoai-osm-rag-agent.git
cd geoai-osm-rag-agent
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-demo.txt
.venv\Scripts\python scripts/run_demo.py
```

On macOS/Linux use `.venv/bin/python`. Open [the local page](http://127.0.0.1:8000/explore). If another app occupies 8000, pass `--port 8017` and open [port 8017](http://127.0.0.1:8017/explore). The launcher prints the requested address and does not kill existing processes. Stop it with Ctrl+C.

Supported place: Lund only. Distance is above zero and at most 2,000 m; bands are `0-4`, `5-9`, `10-14`, `15-19`; minimum children is an integer at least one. Preview, then run. Changing fields invalidates the preview and hides the previous result. The page shows actual request state, without simulated tool progress.

Use the same service from the command line:

```powershell
.venv\Scripts\python scripts/run_analysis_demo.py --distance-m 1000 --age-band 5-9 --minimum-children 10
```

All three parameters are required. Output lives in `output/demo_runs/<run_id>/`: standalone HTML, CSV/GeoJSON and `result.json`. Registries are temporary; successful artifacts persist across restart with no automatic expiry. Keep this directory to retain reports. Remove only specifically selected demo runs when no longer needed; the service never cleans original output. Failed runs are not exposed as completed results.

## View the saved analysis

Python's standard library is enough:

```powershell
python scripts/build_public_demo.py
python -m http.server 8080 --bind 127.0.0.1 --directory build/public_demo
```

Open [the rendered saved example](http://127.0.0.1:8080/). Use HTTP, since the page fetches adjacent JSON. School startup also serves the built site at `/saved/index.html`, exercising relative resources under a subpath. The [public saved analysis](https://xi2618zh-s.github.io/geoai-osm-rag-agent/) is also available without installation; a GitHub blob page shows source instead of the demo.

## HTTP API

`POST /api/analyses/preview` verifies data and returns a plan without GIS execution. `POST /api/analyses/run` rebuilds and executes it. Both accept:

```json
{"scenario":"school_coverage","place":"Lund","distance_m":1000,"age_band":"5-9","minimum_children":10}
```

Alternatively send only `{"command":"find school coverage gaps farther than 1000 m where children aged 5-9 are at least 10 in Lund"}`. Fields and command are mutually exclusive. This is a restricted grammar, not an unrestricted language assistant. Missing/invalid slots return clarification or a request error; other commands return unsupported. Correct the fields and resubmit; no business parameters are silently supplied.

Success includes question, parameters, real execution records, checks, sources and file URLs. `GET /api/analyses/<run_id>` reloads a result, `/report` renders its report and `/files/<artifact_name>` downloads registered files only. Unknown/incomplete runs return 404. Validation failure exposes no success report. Valid zero-result runs export an empty result.

The loader verifies pinned manifests and their files every time. If changed or missing, restore the tracked snapshot rather than editing hashes. Errors keep the form and hide previous results. Unexpected exception details go to the local log. The server rejects browser requests from another Origin. Do not expose this development app publicly.

## OSM place queries

The separate original path requires the full environment, Ollama, a Sweden PBF and osmium CLI:

```powershell
scripts\setup_env.cmd
conda run -n geoai_project_env python scripts\fetch_osm_data.py
ollama pull qwen2.5:3b
scripts\run_local.cmd
```

Open `/ui` on the printed address. The full app also mounts `/explore` but retains its older import/setup requirements. School-only startup provides the school page and links to these instructions; it does not present the original endpoints as active tools.

## Verification

In the full project environment:

```powershell
python -m unittest tests.test_analysis_service tests.test_m12a_scenario -v
python scripts/freeze_m12a.py verify
python -m scripts.audit_m12a_reproducibility
python -m unittest discover -s tests -v
python scripts/check_reproducibility.py --ci
```

The full suite/audits need full dependencies, not only demo requirements. They do not authorize sealed private reruns. The school audit uses public data temporarily and compares existing receipts without overwriting them. Actual acceptance results and exceptions are in [the verification record](VERIFICATION.md).
