@echo off
chcp 65001 >nul
rem 端口可用环境变量覆盖:set PORT=8020 && start_web.bat
rem 默认 8000;job-agent 工作台默认走 8010,两个控制台可同时开
if "%PORT%"=="" set PORT=8000
echo ========================================================
echo     Issue-PR-Agent · 自主编码战术遥测控制台 启动中...
echo ========================================================
echo.
echo 访问地址: http://127.0.0.1:%PORT%
echo.

start "" "http://127.0.0.1:%PORT%"
uv run uvicorn src.webapp:app --host 127.0.0.1 --port %PORT% --reload
pause
