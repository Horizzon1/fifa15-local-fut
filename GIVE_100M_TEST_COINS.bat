@echo off
setlocal
cd /d "%~dp0"
title FIFA 15 Local FUT - 100M Test Coins

echo.
echo  FIFA 15 Local FUT - test coin grant
echo  ==================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo  Setting up the local Python environment first...
    python -m venv .venv
    if errorlevel 1 goto nopython
    ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
    ".venv\Scripts\python.exe" -m pip install --quiet frida cryptography
)

if not exist "server\fifa15-player-catalog.json" (
    echo  Building the FIFA 15 player catalog from your game files...
    ".venv\Scripts\python.exe" tools\extract_catalog.py
    if errorlevel 1 goto failed
    echo.
)

".venv\Scripts\python.exe" tools\give_test_coins.py %*
if errorlevel 1 goto failed

echo.
echo  Done. Start the game and the coins will be in your club.
goto end

:nopython
echo.
echo  Python was not found. Install Python 3.11 or newer and tick
echo  "Add Python to PATH", then run this file again.
goto end

:failed
echo.
echo  Something went wrong. The message above says what.

:end
echo.
pause
