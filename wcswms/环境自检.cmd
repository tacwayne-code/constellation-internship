@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist ".venv\Scripts\python.exe" (
  echo Python environment is missing. Run scripts\setup.ps1 first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" scripts\doctor.py --live --output data\environment-report.json
pause
