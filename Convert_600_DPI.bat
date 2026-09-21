@echo off
setlocal
cd /d "%~dp0"
title PDF 600 DPI Converter

echo ========================================
echo        PDF 600 DPI CMD CONVERTER
echo ========================================
echo.

if "%~1"=="" (
    echo Drag and drop PDF file onto this BAT file.
    echo.
    pause
    exit /b 1
)

python --version >nul 2>&1
if errorlevel 1 (
    echo Python not found. Install Python 3.10+ and tick "Add Python to PATH".
    echo.
    pause
    exit /b 1
)

python -m pip install -r requirements.txt
if errorlevel 1 (
    echo Failed to install required packages.
    pause
    exit /b 1
)

python convert_600dpi.py %*
endlocal
