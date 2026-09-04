@echo off
chcp 65001 >nul
title 交付控制塔 - 统一运营看板
cd /d "%~dp0frontend"

if not exist node_modules (
  echo 正在首次安装依赖，请稍候...
  call npm install
  if errorlevel 1 goto :error
)

echo.
echo 统一运营看板已启动：
echo http://127.0.0.1:5173/
echo.
echo 请保持本窗口开启。按 Ctrl+C 可停止服务。
call npm run dev -- --host 127.0.0.1
exit /b 0

:error
echo.
echo 启动失败，请确认已安装 Node.js 20 或更高版本。
pause
exit /b 1
