# School explorer verification

The school explorer was accepted locally on Windows with Python 3.10. The saved website and the new-calculation page use the same presentation resources, with their different execution modes stated explicitly.

## Execution and browser checks

The default question (1,000 m, ages 5–9, at least ten children) was executed using the included public SCB and OSM files. It returned eleven gap cells and 178 reported children, matching the archived snapshot. Changing distance to 1,500 m and the minimum to twenty returned one cell with 26 children. These values were computed by the existing GIS tools, not filled from reference answers.

Integration checks cover independent concurrent runs, parameter propagation, missing and invalid inputs, valid empty results, changed data/manifest rejection, tool failures, report-evidence rejection and download restrictions. Outputs remain available after temporary registry cleanup and from a newly started server.

Actual desktop and 390px browser checks exercised layer toggles, feature inspection, table scrolling, CSV download, source details, standalone HTML rendering, keyboard focus, parameter invalidation and browser back navigation. Narrow-screen content has no page-wide horizontal overflow. The static site was tested under a subpath as well as at its root.

Screenshots: [saved result](assets/school_coverage.jpg), [narrow screen](assets/school_coverage_mobile.jpg), [new calculation](assets/school_coverage_local.jpg).

## Regression and evidence preservation

Local acceptance checked **572 tests across 50 modules**, with no skips and no unresolved failures after two explicitly recorded retries. The first single-process suite was interrupted by host memory limits. The subsequent isolated run had one temporary freeze check reject source files that were still being edited, and one existing module exceed the initial 180-second limit. After source stabilisation those modules passed unchanged; the slower module took 191.530 seconds. This is an isolated-run-plus-retries result, not a claim that the first command passed.

The frozen school manifest, public thirteen-case reproducibility audit, archived report/download hashes and repository delivery audit passed. A local byte baseline checked 571 protected files without changes. Existing formal experiments were not overwritten. New product code does not import the evaluation harness, oracle or private reference answers. Earlier tests of private-data isolation are part of the existing full suite; private questions were not used to develop the new interface.

Reproduce the relevant checks from a full project environment:

```powershell
python -m unittest tests.test_analysis_service tests.test_m12a_scenario -v
python scripts/freeze_m12a.py verify
python -m scripts.audit_m12a_reproducibility
python scripts/check_reproducibility.py --ci
python -m unittest discover -s tests -v
```

For constrained-memory machines, `python scripts/run_offline_isolated.py --output output/offline_checks` runs the same test modules in separate processes. Full regressions need `requirements.txt`; the school-only install is intentionally smaller. Private files are absent from a public clone, so the existing conditional private-file checks may be skipped there.

Remote CI and Pages deployment results should be judged from the repository's [Actions runs](https://github.com/xi2618zh-s/geoai-osm-rag-agent/actions). A successful static deployment is separate from a passing regression run.
