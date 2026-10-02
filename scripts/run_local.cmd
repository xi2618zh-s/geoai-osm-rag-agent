@echo off
setlocal
cd /d "%~dp0\.."

where conda >nul 2>nul
if errorlevel 1 (
  echo [ERROR] conda was not found on PATH.
  exit /b 1
)

if not defined GEOAI_OSM_PBF if exist "data\osm\sweden-latest.osm.pbf" set "GEOAI_OSM_PBF=%CD%\data\osm\sweden-latest.osm.pbf"

echo [1/2] Verifying local PBF, index, osmium and Ollama...
call conda run -n geoai_project_env python verify_installation.py --ollama
if errorlevel 1 (
  echo [ERROR] Preflight failed. See README.md for the exact recovery steps.
  exit /b 1
)

echo [2/2] Starting GeoAI at http://127.0.0.1:8000/ui
call conda run -n geoai_project_env python app_min.py
