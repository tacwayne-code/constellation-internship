@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
".venv\Scripts\python.exe" scripts\doctor-wms.py %*
set "checkExit=%ERRORLEVEL%"
pause
exit /b %checkExit%
