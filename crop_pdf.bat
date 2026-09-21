@echo off
setlocal EnableDelayedExpansion

:: ─────────────────────────────────────────────────────────────────
::  PDF Crop Engine — Drag-and-Drop Launcher
::  Usage: drag a PDF file onto this BAT file
::
::  Success path : CMD window closes automatically after 2 seconds.
::  Failure path : CMD window stays open for debugging.
:: ─────────────────────────────────────────────────────────────────

:: Locate the Python script next to this BAT file
set "SCRIPT_DIR=%~dp0"
set "ENGINE=%SCRIPT_DIR%pdf_crop_engine.py"

echo.
echo  ╔══════════════════════════════════╗
echo  ║       PDF Crop Engine            ║
echo  ╚══════════════════════════════════╝
echo.

:: ── 1. Check that a file was actually dropped ────────────────────
if "%~1"=="" (
    echo  [ERROR] No PDF file dropped.
    echo.
    echo  How to use:
    echo    Drag and drop a PDF file onto this BAT file.
    echo.
    goto :fail
)

:: ── 2. Capture the dropped PDF path ──────────────────────────────
set "PDF_PATH=%~1"

:: ── 3. Verify it is a PDF ─────────────────────────────────────────
if /i not "%~x1"==".pdf" (
    echo  [ERROR] Dropped file is not a PDF:
    echo    %PDF_PATH%
    echo.
    goto :fail
)

:: ── 4. Verify the file exists ─────────────────────────────────────
if not exist "%PDF_PATH%" (
    echo  [ERROR] File not found:
    echo    %PDF_PATH%
    echo.
    goto :fail
)

:: ── 5. Detect the folder containing the PDF ──────────────────────
set "PDF_DIR=%~dp1"
if "%PDF_DIR:~-1%"=="\" set "PDF_DIR=%PDF_DIR:~0,-1%"

:: ── 6. Build output folder name: <pdf_name>_cropped_bmp ──────────
set "PDF_NAME=%~n1"
set "OUTPUT_DIR=%PDF_DIR%\%PDF_NAME%_cropped_bmp"

:: ── 7. Show what is about to run ──────────────────────────────────
echo  Input PDF   : %PDF_PATH%
echo  Output dir  : %OUTPUT_DIR%
echo  Engine      : %ENGINE%
echo.

:: ── 8. Verify the engine script exists ───────────────────────────
if not exist "%ENGINE%" (
    echo  [ERROR] Engine script not found:
    echo    %ENGINE%
    echo.
    echo  Make sure pdf_crop_engine.py is in the same folder as this BAT file.
    echo.
    goto :fail
)

:: ── 9. Run the Python crop engine ────────────────────────────────
echo  Running...
echo  ──────────────────────────────────────
python "%ENGINE%" "%PDF_PATH%" "%OUTPUT_DIR%"
set "EXIT_CODE=%errorlevel%"
echo  ──────────────────────────────────────

:: ── 10. Route to success or failure ──────────────────────────────
if %EXIT_CODE%==0 (
    goto :success
) else (
    goto :fail
)


:: ═════════════════════════════════════════════════════════════════
:success
::  All pages processed — show summary, wait 2 s, auto-close.
:: ═════════════════════════════════════════════════════════════════
echo.
echo  Processing complete.
echo  Output saved to:
echo    %OUTPUT_DIR%
echo.
echo  This window will close in 2 seconds...
timeout /t 2 /nobreak >nul
goto :done


:: ═════════════════════════════════════════════════════════════════
:fail
::  Something went wrong — keep window open for debugging.
:: ═════════════════════════════════════════════════════════════════
echo.
echo  ──────────────────────────────────────
echo  [ERROR] Processing did not complete successfully.
echo  Review the output above for details.
echo  ──────────────────────────────────────
echo.
pause
goto :done


:: ═════════════════════════════════════════════════════════════════
:done
endlocal
