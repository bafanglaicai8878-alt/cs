@echo off
chcp 65001 >nul
cd /d "%~dp0"

set PY=
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
if not defined PY where python >nul 2>nul && set PY=python
if not defined PY where py >nul 2>nul && set PY=py -3

if not defined PY (
    echo 未找到 Python，请先运行 deploy.bat 或安装 Python 3.10+
    pause
    exit /b 1
)

%PY% -c "import setup_deploy; setup_deploy.apply_deploy(verbose=False)" 2>nul

echo 首次使用建议先运行 check_and_start_gui.bat 自动检测依赖
echo 启动游戏盒子（账号/VIP 连线上服务器，见 config.json 中 Box_Server_URL）
echo.

%PY% -m pip install -q -r requirements.txt 2>nul
%PY% frontend_box.py
pause
