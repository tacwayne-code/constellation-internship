@echo off
setlocal
cd /d "%~dp0"
echo WMS 1.6.0 device-IP update and restart
echo Finish running tasks and station handovers before updating. Queued tasks are kept.
echo Do not submit new tasks from other devices during this update.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\update-device-ip.ps1" %*
set "updateExit=%ERRORLEVEL%"
if not "%updateExit%"=="0" (
  echo Update FAILED. Keep the error shown above.
) else (
  if /I "%~1"=="-CheckOnly" (
    echo Preflight only. Running service was not changed.
  ) else (
    echo Update finished. Refresh the browser with Ctrl+F5.
  )
)
if /I not "%~1"=="-CheckOnly" pause
exit /b %updateExit%
