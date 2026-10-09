@echo off
cd /d "%~dp0"
set "PY=python"
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%~dp0data\logs" mkdir "%~dp0data\logs"
echo Prism: http://127.0.0.1:8848
"%PY%" -m uvicorn app.main:app --host 0.0.0.0 --port 8848 --no-use-colors > "%~dp0data\logs\service.log" 2>&1
echo Server stopped. See data\logs\service.log for details.
pause
