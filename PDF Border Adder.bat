@echo off
setlocal enabledelayedexpansion

rem Check if a file was provided via drag-and-drop or command line
if "%~1" == "" (
    echo ===================================================
    echo                PDF Border Adder
    echo ===================================================
    echo.
    echo Error: No PDF file specified.
    echo.
    echo Usage: Drag and drop a PDF file onto this batch file.
    echo.
    echo ===================================================
    pause
    exit /b 1
)

set "PDF_PATH=%~1"

rem Check if Python is installed
where python >nul 2>nul
if errorlevel 1 (
    echo Error: Python is not installed or not in your system PATH.
    echo Please install Python and try again.
    pause
    exit /b 1
)

rem Check if PyMuPDF (fitz) is installed, install if missing
python -c "import fitz" >nul 2>nul
if errorlevel 1 (
    echo PyMuPDF is missing. Attempting to install via pip...
    python -m pip install pymupdf
    python -c "import fitz" >nul 2>nul
    if errorlevel 1 (
        echo Error: Failed to install PyMuPDF automatically.
        echo Please run: pip install pymupdf
        pause
        exit /b 1
    )
    echo PyMuPDF successfully installed.
    echo.
)

rem Prompt for border dimensions in mm
echo Enter border widths in millimeters (mm):
set /p "TOP=Enter Top Border (mm): "
set /p "RIGHT=Enter Right Border (mm): "
set /p "LEFT=Enter Left Border (mm): "
set /p "BOTTOM=Enter Bottom Border (mm): "

rem Set defaults to 0 if inputs are empty
if "!TOP!" == "" set "TOP=0"
if "!RIGHT!" == "" set "RIGHT=0"
if "!LEFT!" == "" set "LEFT=0"
if "!BOTTOM!" == "" set "BOTTOM=0"

echo.
echo Processing...
python "%~dp0border_pdf.py" --input "!PDF_PATH!" --top !TOP! --right !RIGHT! --left !LEFT! --bottom !BOTTOM!

if errorlevel 1 (
    echo.
    echo An error occurred during processing.
) else (
    echo.
    echo Done.
)

pause
