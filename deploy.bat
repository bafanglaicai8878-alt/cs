@echo off
chcp 65001 >nul
cd /d "%~dp0"
title CS Steam 一键部署

set PY=
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe
if not defined PY where python >nul 2>nul && set PY=python
if not defined PY where py >nul 2>nul && set PY=py -3

if not defined PY (
    echo [错误] 未找到 Python，请安装 Python 3.10+ 并勾选 Add to PATH
    pause
    exit /b 1
)

echo [*] 安装依赖...
%PY% -m pip install -r requirements.txt -q
if errorlevel 1 (
    echo [错误] 依赖安装失败
    pause
    exit /b 1
)

echo [*] 初始化配置与数据库...
%PY% setup_deploy.py
if errorlevel 1 (
    pause
    exit /b 1
)

echo.
echo [*] 启动 Web 服务（关闭本窗口即停止）...
echo.
for /f "delims=" %%i in ('%PY% -c "import json; c=json.load(open('config.json',encoding='utf-8')); print(c.get('Server',{}).get('public_url','http://127.0.0.1:8787'))"') do set PUBLIC=%%i

start "" "%PUBLIC%/admin/login"
%PY% web_server.py --host 0.0.0.0 --port 8787 --public-url %PUBLIC%
pause
