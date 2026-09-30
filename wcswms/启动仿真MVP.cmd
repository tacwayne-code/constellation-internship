@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Python environment is missing.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" run_simulation.py
pause
