@echo off
setlocal
cd /d "%~dp0"
py -3.12 --version >nul 2>&1
if errorlevel 1 (
 echo Install Python 3.12 with Python Launcher from python.org first.
 pause
 exit /b 1
)
if not exist .venv\Scripts\python.exe py -3.12 -m venv .venv
if errorlevel 1 goto failed
if not exist .venv\ready (
 .venv\Scripts\python -m pip install -r requirements.txt
 if errorlevel 1 goto failed
 echo ready>.venv\ready
)
.venv\Scripts\python check_search.py
if errorlevel 1 goto failed
pause
exit /b 0
:failed
echo Check the message above. A blocked site is not bypassed.
pause
exit /b 1
