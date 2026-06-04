@echo off
setlocal
cd /d "%~dp0"

echo MediaRenamer setup
echo.

where python >nul 2>&1
if errorlevel 1 (
    where py >nul 2>&1
    if errorlevel 1 (
        echo Python was not found on PATH.
        echo Install it from https://www.python.org/downloads/ and enable "Add to PATH".
        pause
        exit /b 1
    )
    set "PYTHON=py -3"
) else (
    set "PYTHON=python"
)

echo Installing requirements...
%PYTHON% -m pip install -r requirements.txt
if errorlevel 1 (
    echo Failed to install requirements.
    pause
    exit /b 1
)

echo.
echo Starting MediaRenamer at http://127.0.0.1:8765
echo Close this window to stop the server.
echo.
%PYTHON% main.py

if errorlevel 1 (
    echo.
    echo MediaRenamer exited with an error.
    pause
    exit /b 1
)
