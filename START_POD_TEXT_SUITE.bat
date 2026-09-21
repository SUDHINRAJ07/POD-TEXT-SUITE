@echo off
title POD TEXT SUITE Desktop App Launcher
echo Starting POD Text Tool Suite Desktop Application...

cd /d "%~dp0"

:: Check if standalone compiled EXE exists
if exist "%~dp0dist\POD_Text_Suite\POD_Text_Suite.exe" (
    start "" "%~dp0dist\POD_Text_Suite\POD_Text_Suite.exe"
    exit
)

:: Otherwise launch via Python environment
start "" /b pythonw app.py > nul 2>&1
if %errorlevel% neq 0 (
    python app.py
)

exit
