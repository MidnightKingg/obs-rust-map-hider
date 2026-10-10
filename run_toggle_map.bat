@echo off
setlocal EnableDelayedExpansion
title Rust Map Hider In Obs - Created By MidnightKing
color 0B

:: Check for Administrative privileges
net session >nul 2>&1
if %errorLevel% == 0 (
    set "IS_ADMIN=1"
) else (
    set "IS_ADMIN=0"
)

cd /d "%~dp0"

:: ---------------------------------------------------------------------------
:: Resolve a working Python 3.9+ interpreter.
:: Preference order: py -3 (Windows launcher) > python > python3
:: `python` alone can point at Python 2 on some systems, so we verify version.
:: ---------------------------------------------------------------------------

set "PYCMD="

py -3 --version >nul 2>&1
if %errorlevel% equ 0 (
    py -3 -c "import sys; exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
    if !errorlevel! equ 0 (
        set "PYCMD=py -3"
        goto :RunScript
    )
)

python --version >nul 2>&1
if %errorlevel% equ 0 (
    python -c "import sys; exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
    if !errorlevel! equ 0 (
        set "PYCMD=python"
        goto :RunScript
    )
)

python3 --version >nul 2>&1
if %errorlevel% equ 0 (
    python3 -c "import sys; exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
    if !errorlevel! equ 0 (
        set "PYCMD=python3"
        goto :RunScript
    )
)

:: Nothing usable found -> install
goto :InstallPython

:InstallPython
echo.
echo Python 3.9+ was not found on this system.
echo Attempting automatic installation...
echo.

if "!IS_ADMIN!"=="0" (
    echo Requesting Administrative privileges to install Python...
    powershell -Command "Start-Process '%~0' -Verb RunAs"
    exit /b
)

:: Silent install command
echo Installing Python 3.12... (This may take a few minutes)
winget install -e --id Python.Python.3.12 --scope machine --accept-source-agreements --accept-package-agreements --override "/quiet InstallAllUsers=1 PrependPath=1 Include_test=0"

if %errorlevel% neq 0 (
    echo.
    echo Installation failed.
    echo Please install Python 3 manually from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

echo.
echo Python installed successfully!
echo Please RESTART this script to apply changes.
pause
exit /b 0

:RunScript
echo.
echo  ============================================================
echo.
echo     O B S   M A P   H I D E R
echo     hide your map from stream snipers
echo.
echo     by MidnightKing
echo.
echo  ============================================================
echo.
echo Using interpreter: !PYCMD!

echo Checking dependencies...
!PYCMD! -c "import obsws_python, keyboard" >nul 2>&1
if !errorlevel! equ 0 (
    echo All dependencies already installed. Skipping install.
) else (
    echo Missing dependencies. Installing from requirements.txt...
    !PYCMD! -m pip install -r requirements.txt
    if !errorlevel! neq 0 (
        echo.
        echo Failed to install dependencies.
        pause
        exit /b 1
    )
)
echo.
!PYCMD! toggle_map.py
echo.
echo Press any key to exit...
pause >nul
