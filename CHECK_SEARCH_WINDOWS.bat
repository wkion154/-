@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
 echo Run START_WINDOWS.bat first and configure search sources, then close the app.
 pause
 exit /b 1
)
echo Close K-IDOL ARCHIVE before running this check.
echo This check uses existing permissions. It does not grant permissions or permanently save photos.
echo Enter an artist or group name:
set /p "KIDOL_CHECK_QUERY=> "
if not defined KIDOL_CHECK_QUERY exit /b 1
.venv\Scripts\python -m app.search_check --output search-pipeline-report.json
set "KIDOL_CHECK_EXIT=%ERRORLEVEL%"
echo Report: %CD%\search-pipeline-report.json
pause
exit /b %KIDOL_CHECK_EXIT%
