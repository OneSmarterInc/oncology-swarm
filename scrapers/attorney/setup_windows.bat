@echo off
cd /d "%~dp0"
py -3.12 -m venv .venv
if errorlevel 1 goto fail
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
if errorlevel 1 goto fail
.venv\Scripts\python.exe -m pytest -q
if errorlevel 1 goto fail
echo Setup and automated tests completed. Run run_ui.bat to open the platform.
pause
exit /b 0
:fail
echo Setup or tests failed. Read the error above before continuing.
pause
exit /b 1
