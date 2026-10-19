@echo off
REM One-time setup on Windows. Run from this folder.
py -3.12 -m venv .venv || py -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
echo.
echo Optional, for JavaScript-only hospital sites:
echo   .venv\Scripts\python.exe -m pip install -r requirements-optional.txt
echo   .venv\Scripts\python.exe -m playwright install chromium
echo.
echo Setup done. Start the UI with run_ui.bat
