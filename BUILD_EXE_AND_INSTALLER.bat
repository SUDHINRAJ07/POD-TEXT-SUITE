@echo off
title POD Text Suite Build & Installer System
echo ===================================================
echo   POD TEXT SUITE - EXE ^& INSTALLER BUILD SYSTEM
echo ===================================================
echo.

cd /d "%~dp0"

echo [1/3] Checking PyInstaller installation...
python -m PyInstaller --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Installing PyInstaller...
    python -m pip install pyinstaller
)

echo.
echo [2/3] Building Executable with PyInstaller...
python -m PyInstaller --noconfirm POD_Text_Suite.spec

if %errorlevel% neq 0 (
    echo.
    echo ERROR: PyInstaller build failed!
    pause
    exit /b %errorlevel%
)

echo.
echo [3/3] Compiling Setup Installer with Inno Setup...
if exist "C:\Program Files (x86)\Inno Setup 7\ISCC.exe" (
    "C:\Program Files (x86)\Inno Setup 7\ISCC.exe" setup.iss
) else if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" (
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" setup.iss
) else if exist "C:\Program Files\Inno Setup 6\ISCC.exe" (
    "C:\Program Files\Inno Setup 6\ISCC.exe" setup.iss
) else (
    echo WARNING: Inno Setup ISCC.exe not found.
)

echo.
echo ===================================================
echo SUCCESS!
echo - Standalone EXE directory: dist\POD_Text_Suite\
echo - Installer EXE file: installer_output\POD_Text_Suite_Setup.exe
echo ===================================================
echo.
pause
