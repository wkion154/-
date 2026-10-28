@echo off
setlocal
cd /d "%~dp0"
py -3.12 --version >nul 2>&1
if errorlevel 1 (
 echo Python 3.12 is required. Install it from https://www.python.org/downloads/windows/
 pause
 exit /b 1
)
if not exist .venv\Scripts\python.exe py -3.12 -m venv .venv
if errorlevel 1 goto failed
if not exist .venv\kidol-0.7.4-ready (
 .venv\Scripts\python -m pip install -r requirements.lock
 if errorlevel 1 goto failed
 echo ready>.venv\kidol-0.7.4-ready
)
.venv\Scripts\python -m app.main
if errorlevel 1 goto failed
exit /b 0
:failed
echo Startup failed. See the error above.
pause
exit /b 1
