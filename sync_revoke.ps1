# 自动同步已回收 CDK：删除本地解锁插件（禁玩）
# 静默: powershell -File sync_revoke.ps1 -Quiet
# 安装定时: powershell -File sync_revoke.ps1 -RegisterWatch
# 托管: irm https://你的域名/sync-revoke.ps1 | iex

param(
    [switch]$Quiet,
    [switch]$RegisterWatch
)

$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = "Continue"
$BoxApiBase = "__INJECT_API_BASE__"

function Write-Log([string]$Msg, [string]$Color = "Gray") {
    if ($Quiet) { return }
    Write-Host $Msg -ForegroundColor $Color
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

function Get-ApiBase {
    $hookFile = Join-Path $env:LOCALAPPDATA "steam\hook.json"
    if (Test-Path $hookFile) {
        try {
            $hook = Get-Content $hookFile -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($hook.api) { return ([string]$hook.api).TrimEnd("/") }
        } catch {}
    }
    $api = $BoxApiBase
    if ($api -like "*INJECT*") { $api = $env:CDK_API }
    if (-not $api) { $api = "http://127.0.0.1:8787" }
    return $api.TrimEnd("/")
}

function Get-CdkIndexEntries([string]$Stplug) {
    $idx = Join-Path $Stplug "cdk_index.json"
    if (-not (Test-Path $idx)) { return @() }
    try {
        $raw = Get-Content $idx -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($null -eq $raw) { return @() }
        if ($raw -is [System.Array]) { return @($raw) }
        return @($raw)
    } catch {
        return @()
    }
}

function Save-CdkIndex([string]$Stplug, [array]$Entries) {
    $idx = Join-Path $Stplug "cdk_index.json"
    if ($Entries.Count -eq 0) {
        if (Test-Path $idx) { Remove-Item $idx -Force -ErrorAction SilentlyContinue }
        return
    }
    $Entries | ConvertTo-Json -Depth 4 | Set-Content $idx -Encoding UTF8
}

function Remove-GamePlugins([string]$Stplug, [string]$AppId) {
    $removed = @()
    foreach ($name in @("$AppId.lua", "$AppId.st")) {
        $f = Join-Path $Stplug $name
        if (Test-Path $f) {
            Remove-Item $f -Force -ErrorAction SilentlyContinue
            $removed += $name
        }
    }
    return $removed
}

function Register-RevokeWatchTask([string]$ScriptPath) {
    $taskName = "SteamCDKRevokeSync"
    $tr = "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$ScriptPath`" -Quiet"
    schtasks /Create /TN $taskName /TR $tr /SC MINUTE /MO 5 /F 2>$null | Out-Null
}

function Install-SelfScript([string]$ApiBase) {
    $dir = Join-Path $env:LOCALAPPDATA "steam"
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $local = Join-Path $dir "sync_revoke.ps1"
    try {
        Invoke-WebRequest -Uri "$ApiBase/sync-revoke.ps1" -OutFile $local -UseBasicParsing -TimeoutSec 60
    } catch {}
    return $local
}

function Invoke-RevokeSync {
    $api = Get-ApiBase
    $steamPath = Get-SteamPath
    if (-not $steamPath) {
        Write-Log "未找到 Steam" "Yellow"
        return
    }
    $stplug = Join-Path $steamPath "config\stplug-in"
    if (-not (Test-Path $stplug)) {
        return
    }

    $entries = @(Get-CdkIndexEntries $stplug)
    if ($entries.Count -eq 0) {
        return
    }

    $cdkPattern = '^[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$'
    $cdks = @($entries | ForEach-Object { ([string]$_.cdk).Trim().ToUpper() } | Where-Object { $_ -match $cdkPattern } | Select-Object -Unique)
    if ($cdks.Count -eq 0) {
        return
    }

    try {
        $body = @{ cdks = $cdks } | ConvertTo-Json
        $uri = "$api/api/public/revoked/check"
        $resp = Invoke-RestMethod -Uri $uri -Method Post -Body $body -ContentType "application/json; charset=utf-8" -TimeoutSec 30
    } catch {
        Write-Log "无法连接服务器，跳过自动禁玩同步" "Yellow"
        return
    }

    if (-not $resp.ok) {
        return
    }

    $revoked = @($resp.revoked)
    if ($revoked.Count -eq 0) {
        return
    }

    $removedApps = @()
    $revokedCdks = @($revoked | ForEach-Object { ([string]$_.cdk).ToUpper() })

    foreach ($item in $revoked) {
        $appId = [string]$item.appid
        if (-not $appId) { continue }
        $files = Remove-GamePlugins $stplug $appId
        if ($files.Count -gt 0) {
            $removedApps += $appId
            Write-Log "已禁玩 AppID $appId（移除 $($files -join ', ')）" "Green"
        }
    }

    $remaining = @($entries | Where-Object { $revokedCdks -notcontains ([string]$_.cdk).ToUpper() })
    Save-CdkIndex $stplug $remaining

    if ($removedApps.Count -gt 0 -and (Get-Process -Name "steam" -ErrorAction SilentlyContinue)) {
        Stop-Process -Name "steam*" -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
        Start-Process -FilePath (Join-Path $steamPath "steam.exe") -WorkingDirectory $steamPath | Out-Null
        Write-Log "已重启 Steam 使禁玩生效" "Cyan"
    }
}

$ApiBase = Get-ApiBase
$selfPath = $MyInvocation.MyCommand.Path
if (-not $selfPath) { $selfPath = Join-Path $env:LOCALAPPDATA "steam\sync_revoke.ps1" }

if ($RegisterWatch) {
    $local = Install-SelfScript $ApiBase
    if (Test-Path $local) {
        Register-RevokeWatchTask $local
        Write-Log "已安装自动禁玩同步（每 5 分钟检查）" "Green"
    }
    Invoke-RevokeSync
    exit 0
}

Invoke-RevokeSync
