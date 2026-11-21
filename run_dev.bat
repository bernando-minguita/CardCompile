@echo off
REM Development launcher for CardCompile
REM Requires Python 3.14 with PySide6, Pillow and PyMuPDF installed.
setlocal
cd /d "%~dp0"
echo Starting CardCompile (development mode)...
py -3.14 main.py %*
if errorlevel 1 (
    echo.
    echo The application exited with an error. See logs\application.log
    pause
)
endlocal
