@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
 echo Run setup_windows.bat first.
 pause
 exit /b 1
)
echo Open http://127.0.0.1:8002 in your browser.
.venv\Scripts\python.exe -m attorneyscraper serve --port 8002
pause
