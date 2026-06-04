@echo off
cd /d "%~dp0"
title Download PLAYGAME source to Desktop

set URL=https://steamo.icu/static/playgame-client.zip
set DESKTOP=%USERPROFILE%\Desktop
set OUT=%DESKTOP%\playgame-client.zip
set FOLDER=%DESKTOP%\playgame-client

echo Downloading source to Desktop...
echo URL: %URL%
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ProgressPreference='SilentlyContinue';" ^
  "try { Invoke-WebRequest -Uri '%URL%' -OutFile '%OUT%' -UseBasicParsing; Write-Host '[OK] Saved:' '%OUT%'; exit 0 } catch { Write-Host '[FAIL]' $_.Exception.Message; exit 1 }"

if errorlevel 1 (
    echo.
    echo Download failed. Open in browser:
    echo %URL%
    pause
    exit /b 1
)

echo.
echo Extracting to %FOLDER% ...
powershell -NoProfile -Command ^
  "if (Test-Path '%FOLDER%') { Remove-Item -Recurse -Force '%FOLDER%' }; Expand-Archive -Path '%OUT%' -DestinationPath '%DESKTOP%' -Force"

echo.
echo Done.
echo   Zip:    %OUT%
echo   Folder: %FOLDER%
echo.
echo Run: %FOLDER%\check_and_start_gui.bat
echo.
pause
