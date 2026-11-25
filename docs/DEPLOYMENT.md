# Running and publishing GeoAI

Use [LOCAL_DEMO.md](LOCAL_DEMO.md) for the lightweight school scenario and full OSM environment. School startup binds to `127.0.0.1`; select a free port using `--port 8017`. Do not stop another service to free a port.

The static site displays an archived analysis. Run `python scripts/build_public_demo.py` and preview `build/public_demo` with an HTTP server. Only explicitly listed resources, selected report, its downloads and provenance are included. Private holdout, `.claude/`, `.env`, working output and internal notes are excluded. Source records include hashes, question, parameters and data versions. The builder refuses unexpected files already present in its destination.

`.github/workflows/pages.yml` is separate from offline CI and uses manual dispatch only. It uploads the explicit build directory; deployment permissions are scoped to the deploy job. Enable Pages with GitHub Actions and dispatch after the intended commit is reviewed and published. Configuration follows [GitHub's custom-workflow documentation](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages), checked on 2026-11-24. Relative URLs support the repository project path.

Expected address after publication: `https://xi2618zh-s.github.io/geoai-osm-rag-agent/`. This is **pending deployment**, not a verified live demo link. After publication, verify that exact project URL and every download before updating README's main link, About and profile.

Public real-time questions require a separately deployed backend. Static hosting does not execute Python. Resource limits, concurrent jobs, retention and operational security are a separate delivery scope; this change does not deploy that backend. The original service also uses external Nominatim, Ollama and a PBF, excluded from offline CI.

To disable the new full-app entry, remove its Blueprint registration; historical data/reports and endpoints remain. Do not delete original output or rewrite frozen evidence during rollback.
