@echo off
chcp 65001 >nul
cd /d "%~dp0"

set PY=
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
if not defined PY where python >nul 2>nul && set PY=python
if not defined PY where py >nul 2>nul && set PY=py -3

if not defined PY (
    echo 未找到 Python
    pause
    exit /b 1
)

%PY% cdk_cli.py %*
