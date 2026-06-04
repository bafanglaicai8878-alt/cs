# CDK 回收后客户端卸载游戏插件（需管理员）
# 用法: irm http://你的服务器/revoke.ps1?cdk=XXXX-XXXX-XXXX-XXXX | iex
# 或:   $cdk="XXXX-XXXX-XXXX-XXXX"; irm http://你的服务器/revoke.ps1 | iex

$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = "Stop"
$BoxApiBase = "__INJECT_API_BASE__"

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

function Read-CdkCode {
    param([string]$Preset)
    if ($Preset) {
        $c = ($Preset -replace '\s', '').ToUpper()
        if ($c -match '^[A-Z0-9]{4}(?:-[A-Z0-9]{4}){3}$') { return $c }
    }
    if ($env:CDK_CODE) {
        $c = ($env:CDK_CODE -replace '\s', '').ToUpper()
        if ($c -match '^[A-Z0-9]{4}(?:-[A-Z0-9]{4}){3}$') { return $c }
    }
    $typed = Read-Host "请输入已回收的 CDK"
    return ($typed -replace '\s', '').ToUpper()
}

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "请以管理员身份运行" -ForegroundColor Red
    exit 1
}

$ApiBase = $BoxApiBase
if ($ApiBase -like "*INJECT*") { $ApiBase = $env:CDK_API }
if (-not $ApiBase) { $ApiBase = "http://127.0.0.1:8787" }
$ApiBase = $ApiBase.TrimEnd("/")

$CdkCode = Read-CdkCode $cdk
if (-not $CdkCode) { Write-Host "CDK 无效" -ForegroundColor Red; exit 1 }

try {
    $status = Invoke-RestMethod -Uri "$ApiBase/api/public/cdk/status?cdk=$CdkCode" -TimeoutSec 30
} catch {
    Write-Host "无法连接服务器" -ForegroundColor Red
    exit 1
}

if (-not $status.ok) {
    $msg = if ($status.message) { $status.message } else { "CDK 不存在" }
    Write-Host $msg -ForegroundColor Red
    exit 1
}
if (-not $status.revoked) {
    Write-Host "该 CDK 尚未被服务器标记为回收，无法执行本地卸载" -ForegroundColor Yellow
    exit 1
}

$appId = [string]$status.appid
$steamPath = Get-SteamPath
if (-not $steamPath) { Write-Host "未找到 Steam" -ForegroundColor Red; exit 1 }

$stplug = Join-Path $steamPath "config\stplug-in"
$removed = @()
foreach ($name in @("$appId.lua", "$appId.st")) {
    $f = Join-Path $stplug $name
    if (Test-Path $f) {
        Remove-Item $f -Force
        $removed += $name
    }
}

if (Get-Process -Name "steam" -ErrorAction SilentlyContinue) {
    Stop-Process -Name "steam*" -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}
Start-Process -FilePath (Join-Path $steamPath "steam.exe") -WorkingDirectory $steamPath | Out-Null

if ($removed.Count -gt 0) {
    Write-Host "已移除插件: $($removed -join ', ')" -ForegroundColor Green
    Write-Host "游戏 AppID $appId 已无法通过此 CDK 继续游玩" -ForegroundColor Green
} else {
    Write-Host "本地未找到 AppID $appId 的插件文件（可能已手动删除）" -ForegroundColor Yellow
}
