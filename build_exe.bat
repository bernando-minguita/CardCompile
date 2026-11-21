@echo off
REM Build a folder based Windows distribution of CardCompile.
REM Requirements: Python 3.14 with PySide6, Pillow, PyMuPDF and PyInstaller.
setlocal
cd /d "%~dp0"

echo [1/3] Installing build dependencies...
py -3.14 -m pip install --upgrade pyinstaller || goto :error

echo [2/3] Building CardCompile.exe (folder deployment)...
py -3.14 -m PyInstaller --noconfirm --clean CardCompile.spec || goto :error

echo [3/3] Done.
echo Executable: dist\CardCompile\CardCompile.exe
goto :eof

:error
echo.
echo Build failed. Check the output above.
exit /b 1
