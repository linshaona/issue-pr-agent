@echo off
chcp 65001 >nul
echo ========================================================
echo     Issue-PR-Agent · 自主编码战术遥测控制台 启动中...
echo ========================================================
echo.
echo 访问地址: http://127.0.0.1:8000
echo.

start "" "http://127.0.0.1:8000"
uv run uvicorn src.webapp:app --host 127.0.0.1 --port 8000 --reload
pause
