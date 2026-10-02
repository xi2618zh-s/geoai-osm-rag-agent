@echo off
setlocal
cd /d "%~dp0\.."

where conda >nul 2>nul
if errorlevel 1 (
  echo [ERROR] conda was not found on PATH.
  exit /b 1
)

echo [1/2] Creating or updating geoai_project_env...
call conda env update -n geoai_project_env -f environment.yml
if errorlevel 1 exit /b 1

echo [2/2] Checking Python imports and tracked assets...
call conda run -n geoai_project_env python scripts\check_reproducibility.py --ci
if errorlevel 1 exit /b 1

echo [PASS] Environment is ready.
echo Next: conda run -n geoai_project_env python scripts\fetch_osm_data.py
echo Then: scripts\run_local.cmd
