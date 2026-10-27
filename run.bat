@echo off
setlocal
cd /d "%~dp0"
title Rat Heat Bed
if not exist ".venv\Scripts\python.exe" (
    echo Not installed yet - double-click install.bat first.
    pause
    exit /b 1
)
echo ==================================================
echo   Rat Heat Bed
echo ==================================================
echo   1  Live tracking          - tracks the rat during the run
echo   2  Record, process after  - MPU taps/jumps live, video overlay after
echo   3  Watch tracking on a video file
echo.
choice /c 123 /n /m "Choose 1, 2 or 3: "
set "MODE=%errorlevel%"

if "%MODE%"=="1" .venv\Scripts\python.exe rat_gui.py
if "%MODE%"=="2" .venv\Scripts\python.exe rat_record.py
if "%MODE%"=="3" (
    set "VID="
    set /p "VID=Video file - drag it here, or press Enter for test.mp4: "
    call :watch
)
if errorlevel 1 pause
exit /b

:watch
if defined VID set "VID=%VID:"=%"
.venv\Scripts\python.exe watch_video.py "%VID%"
exit /b
