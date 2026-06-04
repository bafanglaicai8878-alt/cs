@echo off
cd /d "%~dp0"
title PLAYGAME Client

set PY=
if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" set PY=%LOCALAPPDATA%\Programs\Python\Python310\python.exe
if not defined PY where python >nul 2>nul && set PY=python
if not defined PY where py >nul 2>nul && set PY=py -3

if not defined PY (
    echo [FAIL] Python not found. Install Python 3.10+ from python.org
    echo        Check "Add to PATH" and tcl/tk during install.
    pause
    exit /b 1
)

echo [OK] Python:
%PY% --version

%PY% -c "import tkinter; tkinter.Tk().destroy()" 2>nul
if errorlevel 1 (
    echo [FAIL] tkinter missing. Reinstall Python with tcl/tk enabled.
    pause
    exit /b 1
)
echo [OK] tkinter

echo [*] pip install -r requirements.txt
%PY% -m pip install -q -r requirements.txt
if errorlevel 1 (
    echo [FAIL] pip install failed
    pause
    exit /b 1
)
echo [OK] dependencies

%PY% -c "import setup_deploy; setup_deploy.apply_deploy(verbose=False)" 2>nul

echo.
echo Starting PLAYGAME...
echo Dir: %CD%
echo.

if /i "%~1"=="--ui-dev" (
    %PY% frontend_box.py --ui-dev
) else (
    %PY% frontend_box.py %*
)

pause
