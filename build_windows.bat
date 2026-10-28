@echo off
setlocal
cd /d "%~dp0"
if exist build\windows-venv rmdir /s /q build\windows-venv
py -3.12 -m venv build\windows-venv
if errorlevel 1 exit /b 1
build\windows-venv\Scripts\python -m pip install -r requirements.lock
if errorlevel 1 exit /b 1
build\windows-venv\Scripts\python -m pip install pyinstaller==6.12.0
if errorlevel 1 exit /b 1
set QT_QPA_PLATFORM=offscreen
build\windows-venv\Scripts\python -m pytest -q --junitxml=build\windows-tests.xml
if errorlevel 1 exit /b 1
build\windows-venv\Scripts\python -m PyInstaller --noconfirm --clean --windowed --onedir --paths . --name K-IDOL-ARCHIVE --collect-all PIL --exclude-module mediapipe --exclude-module cv2 --exclude-module jax --exclude-module jaxlib scripts\windows_entry.py
if errorlevel 1 exit /b 1
build\windows-venv\Scripts\python scripts\collect_licenses.py dist\K-IDOL-ARCHIVE\LICENSES
if errorlevel 1 exit /b 1
start /wait "" dist\K-IDOL-ARCHIVE\K-IDOL-ARCHIVE.exe --smoke-test build\windows-smoke.json
if errorlevel 1 exit /b 1
copy README.md dist\K-IDOL-ARCHIVE\README.txt
copy CHANGELOG.md dist\K-IDOL-ARCHIVE\CHANGELOG.txt
build\windows-venv\Scripts\python scripts\package.py --windows
if errorlevel 1 exit /b 1
endlocal
