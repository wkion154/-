@echo off
setlocal
cd /d "%~dp0"
py -3.12 -m venv .venv
if errorlevel 1 exit /b 1
.venv\Scripts\python -m pip install -r requirements.lock
if errorlevel 1 exit /b 1
.venv\Scripts\python -m pip install pyinstaller==6.12.0
if errorlevel 1 exit /b 1
.venv\Scripts\python -m pytest -q
if errorlevel 1 exit /b 1
.venv\Scripts\python -m PyInstaller --noconfirm --clean --windowed --onedir --paths . --name K-IDOL-ARCHIVE --collect-all PIL scripts\windows_entry.py
if errorlevel 1 exit /b 1
.venv\Scripts\python scripts\collect_licenses.py dist\K-IDOL-ARCHIVE\LICENSES
if errorlevel 1 exit /b 1
copy README.md dist\K-IDOL-ARCHIVE\README.txt
copy CHANGELOG.md dist\K-IDOL-ARCHIVE\CHANGELOG.txt
.venv\Scripts\python scripts\package.py --windows
if errorlevel 1 exit /b 1
endlocal
