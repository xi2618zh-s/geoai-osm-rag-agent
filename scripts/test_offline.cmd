@echo off
setlocal
cd /d "%~dp0\.."

call conda run -n geoai_project_env python -m compileall -q app_min.py src tests benchmarks scripts
if errorlevel 1 exit /b 1
call conda run -n geoai_project_env python -m unittest discover -s tests
if errorlevel 1 exit /b 1
call conda run -n geoai_project_env python scripts\check_reproducibility.py --ci
if errorlevel 1 exit /b 1

echo [PASS] Offline P0-P5 verification completed.
