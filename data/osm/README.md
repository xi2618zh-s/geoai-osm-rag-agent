# OSM source data

Large `.osm.pbf` files are runtime inputs and are intentionally excluded from Git.

The default source is the official Geofabrik Sweden extract declared in
`source_manifest.json`. From the repository root:

```powershell
conda run -n geoai_project_env python scripts\fetch_osm_data.py
```

Or without a fixed Python path:

```powershell
conda run -n geoai_project_env python scripts\fetch_osm_data.py
```

The downloader first obtains the provider-published MD5, streams into a `.part`
file, verifies the digest, atomically renames the completed file, and writes a
snapshot receipt beside it. Re-running verifies and reuses a valid local file.

MD5 here is a provider-compatible transfer-integrity check, not a cryptographic
signature. For a fixed archived release, pass a pinned URL and an independently
recorded SHA-256 value:

```powershell
python scripts\fetch_osm_data.py `
  --url "https://download.geofabrik.de/europe/sweden-260726.osm.pbf" `
  --output "data/osm/sweden-260726.osm.pbf" `
  --checksum "sha256:YOUR_RECORDED_SHA256"
```

OpenStreetMap data is licensed under ODbL 1.0. Preserve attribution and consult
the provider landing page before redistribution.
