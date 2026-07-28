@echo off
title PLM Server Watchdog
cd /d "C:\Users\15897\WorkBuddy\2026-07-14-15-12-28\plm_system"
set PY=C:\Users\15897\.workbuddy\binaries\python\envs\plm\Scripts\python.exe

:loop
echo [%date% %time%] 启动 PLM 服务...
%PY% run.py
echo [%date% %time%] 服务退出，5秒后自动重启...
timeout /t 5 /nobreak >nul
goto loop