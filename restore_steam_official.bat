@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo  正在将 Steam 恢复为官方干净状态...
echo  将删除: 注入 DLL、stplug-in 解锁插件、SteamTools 注册表
echo.
pause
set PY=
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
if not defined PY where python >nul 2>nul && set PY=python
%PY% restore_steam_official.py
echo.
pause
