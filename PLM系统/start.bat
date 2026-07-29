@echo off
chcp 65001 >nul
echo ========================================
echo   PLM 产品生命周期管理系统 - 启动
echo ========================================
echo.
echo 正在安装依赖...
"C:\Users\15897\.workbuddy\binaries\python\envs\default\Scripts\pip" install -r requirements.txt
echo.
echo 正在启动 PLM 系统...
echo 访问地址: http://localhost:5000
echo 管理员账号: admin / admin123
echo.
"C:\Users\15897\.workbuddy\binaries\python\envs\default\Scripts\python" run.py
pause
