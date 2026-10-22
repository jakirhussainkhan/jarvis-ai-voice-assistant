@echo off
rem ===========================================================================
rem  JARVIS - start the desktop app
rem
rem  Double-click this file to launch Jarvis.
rem  Run  start.bat --console  to keep a console window with the logs.
rem ===========================================================================
setlocal
cd /d "%~dp0"

set "PY=%~dp0venv\Scripts\python.exe"
set "PYW=%~dp0venv\Scripts\pythonw.exe"
set "APP=%~dp0main.py"

if not exist "%APP%" (
    echo [JARVIS] main.py not found next to this file:
    echo          %APP%
    pause
    exit /b 1
)

if not exist "%PY%" (
    echo [JARVIS] Python virtual environment not found:
    echo          %PY%
    echo.
    echo Set it up once with:
    echo     py -3.12 -m venv venv
    echo     venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

if not exist "%PYW%" set "PYW=%PY%"

if /i "%~1"=="--console" (
    echo Starting JARVIS in this window...
    "%PY%" "%APP%" --gui
    exit /b %errorlevel%
)

rem pythonw keeps the desktop clean (no console window). Starting it twice is
rem harmless: main.py detects the running instance and simply wakes it up.
start "" "%PYW%" "%APP%" --gui
exit /b 0
