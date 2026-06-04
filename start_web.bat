@echo off
chcp 65001 >nul
cd /d "%~dp0"

set PY=
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
if not defined PY where python >nul 2>nul && set PY=python
if not defined PY where py >nul 2>nul && set PY=py -3

%PY% setup_deploy.py >nul 2>nul

for /f "delims=" %%i in ('%PY% -c "import json; c=json.load(open('config.json',encoding='utf-8')); print(c.get('Server',{}).get('public_url','http://127.0.0.1:8787'))"') do set PUBLIC=%%i
for /f "delims=" %%i in ('%PY% -c "import json; c=json.load(open('config.json',encoding='utf-8')); print(int(c.get('Server',{}).get('port',8787)))"') do set PORT=%%i

echo.
echo  启动 Web 服务: %PUBLIC%
echo  管理后台: %PUBLIC%/admin/login
echo.

start "" "%PUBLIC%/admin/login"
%PY% web_server.py --host 0.0.0.0 --port %PORT% --public-url %PUBLIC%
