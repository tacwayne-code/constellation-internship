@echo off
chcp 65001 >nul
echo ========================================
echo   PLM 产品生命周期管理系统 - 启动
echo ========================================
echo.
echo 正在安装依赖...
"%~dp0..\..\..\.workbuddy\binaries\python\envs\default\Scripts\pip" install -r requirements.txt 2>nul
echo.
echo 正在启动 PLM 系统...
echo 访问地址: http://localhost:5000
echo 管理员账号: admin / admin123
echo.
REM 内网部署时设置 PLM_HOST=0.0.0.0，否则仅本机可访问
set PLM_HOST=0.0.0.0
REM 内网 Odoo 自签证书环境需跳过 SSL 验证
set PLM_ODOO_INSECURE_SSL=1
"%~dp0..\..\..\.workbuddy\binaries\python\envs\default\Scripts\python" run.py
pause
