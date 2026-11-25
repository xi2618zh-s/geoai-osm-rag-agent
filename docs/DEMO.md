# Explore a school coverage analysis

Build and serve the saved example with Python's standard library:

```powershell
python scripts/build_public_demo.py
python -m http.server 8080 --bind 127.0.0.1 --directory build/public_demo
```

Open [the rendered page](http://127.0.0.1:8080/). It shows **Saved analysis**, not live computation. Public Pages hosting is prepared but pending. GitHub's HTML source viewer is not the demo.

Start with the question: which cells around Lund are over 1,000 metres from an extracted school and contain at least ten children aged 5–9? The saved record identifies eleven cells holding 178 reported children. Toggle population, buffers, schools and gap layers. Select a feature, inspect the table, download CSV or projected GeoJSON, then open the unchanged standalone report and expand sources and limits.

The plan standardises population data, projects schools to metric coordinates, selects schools, draws display buffers, measures polygon-to-point distances and filters gaps. These are extracted points, not verified entrances. Straight-line distance is not walking distance. SCB edge cells and disclosure-control semantics affect interpretation; totals are not municipality population.

To calculate a new result, follow [the local guide](LOCAL_DEMO.md), then open `/explore`. Preview the parameters and data dates. Run: the server executes GIS tools and validates outputs and report evidence before exposing downloads. Change distance to 1,500 metres: the previous preview/result disappear until another preview/run. Increase the child threshold sufficiently to obtain a valid empty result. Each successful calculation has its own run ID and persistent files.

Actual screenshots: [saved analysis](assets/school_coverage.jpg), [390px screen](assets/school_coverage_mobile.jpg), [new local calculation](assets/school_coverage_local.jpg). Checks and limitations of acceptance are recorded in [the verification record](VERIFICATION.md).

The separate OSM experience is available through the full app at `/ui`. It supports `Find all cafes in Lund` and explicit OR queries such as `Find cafes or restaurants in Lund`. Its [historical screenshot](assets/p5_workflow_result.png) records 173 deduplicated café/restaurant objects; this is not a current-count guarantee. The school server links to setup instructions instead of presenting that separate runtime as an active tool.
