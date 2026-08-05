@echo off
chcp 65001 >nul
echo ========================================
echo   PLM系统2 - 产品生命周期管理系统
echo ========================================
echo.
echo 正在启动 PLM系统2...
echo 访问地址: http://localhost:5001
echo 管理员账号: admin / admin123
echo 图号规范: XX-XX-XX-XX (机型-识别号-模块-顺序)
echo 配件编码: XX-XX-XX-XX (大类-产品族-部件-流水)
echo.
REM 内网部署时设置 PLM_HOST=0.0.0.0，否则仅本机可访问
set PLM_HOST=0.0.0.0
REM 内网 Odoo 自签证书环境需跳过 SSL 验证
set PLM_ODOO_INSECURE_SSL=1
REM 端口设为 5001 避免与老PLM冲突
set PLM_PORT=5001
cd /d "%~dp0"
call C:\Users\15897\.workbuddy\binaries\python\envs\default\Scripts\python.exe run.py
pause
