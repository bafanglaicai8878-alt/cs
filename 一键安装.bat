@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo ========================================
echo    Steam 游戏盒子 - 一键安装 ^(CDK^)
echo ========================================
echo.

where powershell >nul 2>nul
if %errorlevel% neq 0 (
    echo 未找到 PowerShell
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
pause
