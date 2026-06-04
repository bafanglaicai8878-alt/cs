@echo off
chcp 65001 >nul
echo ========================================
echo  SteamTools 完整修复脚本
echo ========================================
echo.

set STEAM=d:\steam
set STEAMTOOLS=D:\SteamTools\SteamTools.exe
if not exist "%STEAMTOOLS%" set STEAMTOOLS=C:\SteamTools\SteamTools.exe

echo [1/5] 修正 SteamTools 注册表（此前可能指向 system32）...
reg add "HKCU\Software\Valve\Steamtools" /v SteamPath /t REG_SZ /d "d:/steam" /f >nul
reg add "HKCU\Software\Valve\Steamtools" /v iscdkey /t REG_SZ /d "false" /f >nul

echo [2/5] 关闭 Steam...
taskkill /IM steam.exe /F >nul 2>&1
taskkill /IM steamwebhelper.exe /F >nul 2>&1
timeout /t 2 /nobreak >nul

echo [3/5] 更新 SteamTools 注入文件（官方脚本）...
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://steam.run | iex"

echo [4/5] 启动 SteamTools...
start "" "%STEAMTOOLS%"
timeout /t 4 /nobreak >nul

echo [5/5] 启动 Steam...
start "" "%STEAM%\steam.exe"

echo.
echo 修复完成。请测试: steam://install/1145360
echo 插件目录: %STEAM%\config\stplug-in\  （需同时有 .lua 和 .st）
echo.
pause
