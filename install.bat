@echo off
setlocal
cd /d "%~dp0"
title Rat Heat Bed - install
set "UV_LINK_MODE=copy"
echo ==================================================
echo   Rat Heat Bed - one-time setup
echo   Needs internet. Takes about 1-5 minutes.
echo   Does not change any other Python on this PC.
echo ==================================================
echo.

rem --- 1. uv: downloads its own Python + packages, works on any Windows 10/11 PC ---
set "UV="
where uv >nul 2>nul && set "UV=uv"
if not defined UV if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
if not defined UV (
    echo [1/4] Installing uv package manager...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
)
if not defined UV goto fail
echo [1/4] uv ready.

rem --- 2. Private Python 3.11 in .venv (x86_64 build also runs on ARM PCs via emulation) ---
echo [2/4] Setting up Python 3.11...
if not exist ".venv\Scripts\python.exe" (
    "%UV%" venv .venv --managed-python --python cpython-3.11-windows-x86_64-none
    if errorlevel 1 goto fail
)

rem --- 3. Packages ---
echo [3/4] Installing packages...
"%UV%" pip install --python .venv\Scripts\python.exe -r requirements.txt
if errorlevel 1 goto fail

rem --- 4. Self-test ---
echo [4/4] Checking everything works...
.venv\Scripts\python.exe test_rat_track.py || goto fail
.venv\Scripts\python.exe test_rat_gui.py || goto fail
.venv\Scripts\python.exe test_postprocess.py || goto fail

echo.
echo ==================================================
echo   Done! Double-click run.bat to start.
echo.
echo   Nano not showing up as a port? Most clone Nanos use
echo   the CH340 chip - Windows usually installs its driver
echo   automatically; if not, get "CH341SER" from wch-ic.com.
echo ==================================================
pause
exit /b 0

:fail
echo.
echo *** Setup FAILED - see the message above. ***
echo Check the internet connection and run install.bat again.
pause
exit /b 1
