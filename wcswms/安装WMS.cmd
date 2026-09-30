@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup-wms.ps1" %*
set "setupExit=%ERRORLEVEL%"
if not "%setupExit%"=="0" echo Installation FAILED. Keep the error above.
pause
exit /b %setupExit%
