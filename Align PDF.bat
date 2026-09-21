@echo off
title Align PDF - Smart Page Number Alignment
cd /d "%~dp0"

:: ── Startup banner ───────────────────────────────────────────────────────────
echo.
echo  =========================================================
echo   Align PDF  ^|  Startup Verification
echo  =========================================================
echo.

:: Resolve the script path (always relative to this .bat file's directory)
set "SCRIPT=%~dp0align_pdf.py"

:: Confirm the script file exists
if not exist "%SCRIPT%" (
    echo  ERROR: align_pdf.py not found at:
    echo    %SCRIPT%
    echo.
    goto :fail
)

:: Detect Python executable
set "PYTHON="
python --version >nul 2>&1
if %errorlevel%==0 set "PYTHON=python"
if not defined PYTHON (
    py --version >nul 2>&1
    if %errorlevel%==0 set "PYTHON=py"
)
if not defined PYTHON (
    echo  ERROR: Python not found in PATH.
    echo  Install Python from https://www.python.org/ and make sure
    echo  "Add Python to PATH" is checked during installation.
    echo.
    goto :fail
)

:: Print Python executable path
for /f "delims=" %%P in ('%PYTHON% -c "import sys; print(sys.executable)"') do set "PYEXE=%%P"

:: Print align_pdf.py last-modified timestamp
for /f "delims=" %%T in ('%PYTHON% -c "import os,datetime; t=os.path.getmtime(r'%SCRIPT%'); print(datetime.datetime.fromtimestamp(t).strftime('%%Y-%%m-%%d  %%H:%%M:%%S'))"') do set "MTIME=%%T"

echo  Script   : %SCRIPT%
echo  Python   : %PYEXE%
echo  Modified : %MTIME%
echo  Embed    : DeviceGray only  (Color Image / RGB excluded, no ICC)
echo  DPI      : 600 (fixed - source DPI ignored)
echo.
echo  =========================================================
echo.

if "%~1"=="" (
    echo  Align PDF - Smart Page Number Alignment Tool
    echo  =============================================
    echo.
    echo  Drag and drop a PDF onto this file, OR run:
    echo    "Align PDF.bat"  input.pdf  [output.pdf]
    echo.
    echo  Output is saved as:  input_aligned.pdf
    echo.
    echo  Output uses selected template size.
    echo  Images embedded as plain DeviceGray only.
    echo  Color Image / DeviceRGB excluded. No ICC profile.
    echo.
    pause
    exit /b 0
)

set "INPUT=%~1"
set "OUTPUT=%~dpn1_aligned.pdf"
if not "%~2"=="" set "OUTPUT=%~2"

echo.
echo  Select template page size:
echo.
echo    1) 148 x 210 mm  (3496 x 4961 px @ 600 DPI)
echo    2) 152 x 229 mm  (3591 x 5409 px @ 600 DPI)
echo    3) 156 x 234 mm  (3685 x 5528 px @ 600 DPI)
echo    4) 170 x 244 mm  (4016 x 5764 px @ 600 DPI)
echo    5) 178 x 254 mm  (4205 x 6000 px @ 600 DPI)
echo    6) 210 x 297 mm  (4961 x 7016 px @ 600 DPI)
echo.
set /p "TMPL=  Enter 1-6: "

if "%TMPL%"=="1" ( set "TPL_W=3496" & set "TPL_H=4961" )
if "%TMPL%"=="2" ( set "TPL_W=3591" & set "TPL_H=5409" )
if "%TMPL%"=="3" ( set "TPL_W=3685" & set "TPL_H=5528" )
if "%TMPL%"=="4" ( set "TPL_W=4016" & set "TPL_H=5764" )
if "%TMPL%"=="5" ( set "TPL_W=4205" & set "TPL_H=6000" )
if "%TMPL%"=="6" ( set "TPL_W=4961" & set "TPL_H=7016" )

if not defined TPL_W (
    echo.
    echo  ERROR: Invalid selection. Please enter a number from 1 to 6.
    echo.
    goto :fail
)

echo.
set /p "BORDER_MM=  White edge cleanup border mm [default 3, enter 0 to disable]: "
if "%BORDER_MM%"=="" set "BORDER_MM=3"

echo.
echo  Input    : %INPUT%
echo  Output   : %OUTPUT%
echo  Template : %TPL_W% x %TPL_H% px
echo  Border   : %BORDER_MM% mm
echo.

if not exist "%INPUT%" (
    echo  ERROR: Input file not found:
    echo    %INPUT%
    echo.
    goto :fail
)

:run
echo  Using : %PYEXE%
echo.
%PYTHON% align_pdf.py "%INPUT%" "%OUTPUT%" --tpl-w %TPL_W% --tpl-h %TPL_H% --border-mm %BORDER_MM% 2>&1
if %errorlevel% neq 0 (
    echo.
    echo  ERROR: Processing failed (see messages above).
    echo  If packages are missing, run:
    echo    pip install pymupdf opencv-python numpy
    echo.
    goto :fail
)

echo.
echo  Done. Output saved to:
echo  %OUTPUT%
echo.
pause
exit /b 0

:fail
echo  -------------------------------------------------------
pause
exit /b 1
