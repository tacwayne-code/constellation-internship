@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\test-local.ps1
pause
