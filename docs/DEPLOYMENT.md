# Deployment decision and operating model

## Decision

P5 uses a validated Windows/Conda one-command path as the primary delivery. A
Dockerfile is deliberately not included in this phase because Docker is absent on
the acceptance machine and the true runtime spans three heavyweight boundaries:
an Ollama model, an external `osmium` binary and a roughly 773 MB mutable PBF.
Shipping an unbuilt container would weaken, rather than improve, reproducibility.

## Supported local topology

```text
Browser → Flask 127.0.0.1:8000
              ├→ Ollama 127.0.0.1:11434
              ├→ Nominatim HTTPS
              ├→ osmium CLI
              └→ local Sweden PBF / FAISS / cache / GeoJSON
```

Primary commands:

```bat
scripts\setup_env.cmd
conda run -n geoai_project_env python scripts\fetch_osm_data.py
ollama pull qwen2.5:3b
scripts\run_local.cmd
```

The runner performs preflight before binding the Flask port. It uses `conda run`
so PowerShell profile execution policy cannot break environment activation.

## External-state policy

- PBF is downloaded, verified and ignored by Git.
- Model weights stay in Ollama/Hugging Face caches, outside the repository.
- Nominatim is not mocked in real E2E and therefore cannot have a fixed SLA here.
- CI runs only deterministic offline tests; external E2E is a manual release gate.
- The Flask development server binds to loopback by default and must not be
  exposed directly as a public production service.

## Before a public deployment

Add an authenticated reverse proxy, a production WSGI server, request size/rate
limits, persistent shared cache, centralized logs/metrics, Nominatim usage-policy
review or a self-hosted geocoder, data/model version pinning, vulnerability scans,
backup/retention policy and load testing. None of these are claimed as completed.

## Future container trigger

Containerization becomes worthwhile when a Docker-capable acceptance runner is
available and PBF/model volumes can be mounted rather than baked into the image.
The acceptance gate should include image build, health check, real osmium fixture,
Ollama connectivity and shutdown/volume persistence tests.
