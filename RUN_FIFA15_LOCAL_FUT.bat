@echo off
setlocal
cd /d "%~dp0"
title FIFA 15 Local FUT

echo.
echo  FIFA 15 Local FUT
echo  =================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo  Setting up the local Python environment ^(one time^)...
    python -m venv .venv
    if errorlevel 1 goto nopython
    ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
    ".venv\Scripts\python.exe" -m pip install --quiet frida cryptography
    if errorlevel 1 goto failed
    echo.
)

if not exist "server\fifa15-player-catalog.json" (
    echo  Building the FIFA 15 player catalog from your game files ^(one time^)...
    ".venv\Scripts\python.exe" tools\extract_catalog.py
    if errorlevel 1 goto failed
    echo.
)

echo  Starting the local server and launching FIFA 15.
echo  Nothing needs administrator rights, and no game file is modified.
echo.

".venv\Scripts\python.exe" tools\launch.py %*
if errorlevel 1 goto failed

echo.
echo  Session finished. Logs are in the logs\ folder.
goto end

:nopython
echo.
echo  Python was not found. Install Python 3.11 or newer, tick
echo  "Add Python to PATH" during setup, then run this file again.
goto end

:failed
echo.
echo  Something went wrong. The message above says what.
echo  Full detail is in the logs\ folder.

:end
echo.
pause
