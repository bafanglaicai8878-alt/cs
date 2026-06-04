# 修复「命令显示成功但库里没有」— 清理其他入库工具残留并重装 Hook
# irm http://你的服务器:8787/fix | iex

$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = "Stop"
$BoxApiBase = "__INJECT_API_BASE__"

try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls11 -bor [Net.SecurityProtocolType]::Tls } catch {}

function Get-RemoteFile([string[]]$Urls, [string]$Target, [int]$MinBytes = 100000) {
    $lastErr = $null
    foreach ($url in $Urls) {
        if (-not $url) { continue }
        for ($attempt = 1; $attempt -le 3; $attempt++) {
            try {
                if (Test-Path $Target) { Remove-Item $Target -Force -ErrorAction SilentlyContinue }
                Invoke-WebRequest -Uri $url -OutFile $Target -UseBasicParsing -TimeoutSec 90 -MaximumRedirection 5 | Out-Null
                if ((Test-Path $Target) -and (Get-Item $Target).Length -ge $MinBytes) { return $true }
                $lastErr = "下载内容不完整 ($url)"
            } catch {
                $lastErr = $_.Exception.Message
            }
            Start-Sleep -Seconds (2 * $attempt)
        }
    }
    if (Test-Path $Target) { Remove-Item $Target -Force -ErrorAction SilentlyContinue }
    throw "无法下载激活组件，请检查网络后重试。$lastErr"
}

function Get-SteamPath {
    $steamPath = ""
    if (Test-Path "HKCU:\Software\Valve\Steam") {
        $p = Get-ItemProperty "HKCU:\Software\Valve\Steam" -ErrorAction SilentlyContinue
        if ($p.SteamPath) { $steamPath = [string]$p.SteamPath }
    }
    if (-not $steamPath) {
        $steamPath = (Get-ItemProperty "HKLM:\Software\WOW6432Node\Valve\Steam" -Name "InstallPath" -ErrorAction SilentlyContinue).InstallPath
    }
    foreach ($p in @($steamPath, "D:\steam", "C:\Program Files (x86)\Steam")) {
        if ($p -and (Test-Path (Join-Path $p "steam.exe"))) { return $p }
    }
    return $null
}

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "请以管理员身份运行 PowerShell" -ForegroundColor Red
    exit 1
}

$ApiBase = $BoxApiBase
if ($ApiBase -like "*INJECT*") { $ApiBase = "http://127.0.0.1:8787" }
$ApiBase = $ApiBase.TrimEnd("/")

$steamPath = Get-SteamPath
if (-not $steamPath) {
    Write-Host "未找到 Steam 安装路径" -ForegroundColor Red
    exit 1
}

Write-Host "[*] Steam 路径: $steamPath" -ForegroundColor Cyan
Write-Host "[*] 关闭 Steam …" -ForegroundColor Cyan
Stop-Process -Name "steam*" -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

Write-Host "[*] 清理其他入库工具残留 …" -ForegroundColor Cyan
foreach ($name in @(
    "version.dll", "user32.dll", "steam.cfg", "hid.dll",
    "GreenLuma_2025_x86.dll", "GreenLuma_2025_x64.dll"
)) {
    $f = Join-Path $steamPath $name
    if (Test-Path $f) {
        Remove-Item $f -Force
        Write-Host "  已删除 $name" -ForegroundColor Gray
    }
}

Write-Host "[*] 重装 Hook 注入文件 …" -ForegroundColor Cyan
$pairs = @(
    @("xinput1_4.dll", @("https://update.steamcdn.com/update", "http://update.steamcdn.com/update", "$ApiBase/static/inject/xinput1_4.dll")),
    @("dwmapi.dll", @("https://update.steamcdn.com/dwmapi", "http://update.steamcdn.com/dwmapi", "$ApiBase/static/inject/dwmapi.dll"))
)
foreach ($p in $pairs) {
    $target = Join-Path $steamPath $p[0]
    Get-RemoteFile -Urls $p[1] -Target $target | Out-Null
    Write-Host "  已更新 $($p[0])" -ForegroundColor Green
}

$stplug = Join-Path $steamPath "config\stplug-in"
New-Item -ItemType Directory -Force -Path $stplug | Out-Null
$regPath = "HKCU:\Software\Valve\Steamtools"
if (-not (Test-Path $regPath)) { New-Item -Path $regPath -Force | Out-Null }
foreach ($n in @("ActivateUnlockMode", "AlwaysStayUnlocked", "notUnlockDepot")) {
    Remove-ItemProperty -Path $regPath -Name $n -ErrorAction SilentlyContinue
}
Set-ItemProperty -Path $regPath -Name "SteamPath" -Value (($steamPath -replace "\\", "/")) -Type String
Set-ItemProperty -Path $regPath -Name "iscdkey" -Value "true" -Type String

$idx = Join-Path $stplug "cdk_index.json"
if (Test-Path $idx) {
    try {
        $last = (Get-Content $idx -Raw -Encoding UTF8 | ConvertFrom-Json)[-1]
        if ($last.appid) {
            Write-Host ""
            Write-Host "最近激活 AppID: $($last.appid)  ($($last.name))" -ForegroundColor Cyan
            Write-Host "可尝试: steam://install/$($last.appid)" -ForegroundColor Yellow
        }
    } catch {}
}

Write-Host "[*] 启动 Steam …" -ForegroundColor Cyan
Start-Process -FilePath (Join-Path $steamPath "steam.exe") -WorkingDirectory $steamPath | Out-Null

Write-Host ""
Write-Host "修复完成。请查看库中是否出现游戏。" -ForegroundColor Green
Write-Host "仍没有则重新激活: irm $ApiBase | iex" -ForegroundColor Yellow
Write-Host ""
