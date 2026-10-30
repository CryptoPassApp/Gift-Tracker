# GiftTracker reconcile local stack (idempotent).
#   start_local.ps1            - keep healthy API / tunnel / bot, start what is missing
#   start_local.ps1 -BotOnly   - restart only the bot
#   start_local.ps1 -ApiOnly   - restart only the API (tunnel/bot untouched)
#   start_local.ps1 -Force     - restart everything
# Rules for agents: never kill processes by PID lists; run this script once.
# NOTE: keep this file ASCII-only. Non-ASCII literals break under PS5.1 (no BOM).

param([switch]$Force, [switch]$BotOnly, [switch]$ApiOnly)

$ErrorActionPreference = 'SilentlyContinue'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$logs = Join-Path $root 'logs'
New-Item -ItemType Directory -Force -Path $logs | Out-Null

function Get-CmdProcs([string]$pattern) {
    Get-CimInstance Win32_Process |
        Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -and $_.CommandLine -like $pattern }
}

function Stop-ByCmd([string]$pattern) {
    Get-CmdProcs $pattern | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
}

function Start-Detached([string]$cmd, [string]$workdir) {
    # Hidden launch: wscript runs the matching .vbs (Shell.Run window style 0).
    # Console apps cannot be hidden via Win32_ProcessStartup (ignored) or
    # pythonw (uvicorn crashes with sys.stderr = None).
    return (Invoke-WmiMethod -Class Win32_Process -Name Create `
        -ArgumentList $cmd, $workdir, $null)
}

Write-Host '=== GiftTracker reconcile ==='

$envPath = Join-Path $root '.env'
$envText = Get-Content $envPath -Raw -Encoding UTF8
$m = [regex]::Match($envText, 'MINI_APP_URL=(\S+)')
$currentUrl = if ($m.Success) { $m.Groups[1].Value } else { '' }

if (-not $BotOnly) {
    # ---------- 1. API ----------
    $apiProcs = @(Get-CmdProcs '*uvicorn*api.main*')
    $apiUp = $false
    try {
        $r = Invoke-RestMethod 'http://127.0.0.1:8020/api/health' -TimeoutSec 3
        $apiUp = ($r.status -eq 'ok')
    } catch { }

    if ($apiUp -and $apiProcs.Count -eq 1 -and -not $Force) {
        Write-Host "API: healthy (pid $($apiProcs[0].ProcessId)) - kept"
    } else {
        if ($apiProcs.Count -gt 1) { Write-Host "API: $($apiProcs.Count) instances -> fixing" }
        Stop-ByCmd '*uvicorn*api.main*'
        Start-Sleep 1
        $apiCmd = "wscript.exe //B scripts\_hidden\api.vbs"
        Start-Detached $apiCmd $root | Out-Null
        $apiUp = $false
        for ($i = 0; $i -lt 25; $i++) {
            Start-Sleep 1
            try {
                $r = Invoke-RestMethod 'http://127.0.0.1:8020/api/health' -TimeoutSec 2
                if ($r.status -eq 'ok') { $apiUp = $true; break }
            } catch { }
        }
        if ($apiUp) { Write-Host 'API: started on http://127.0.0.1:8020' }
        else { Write-Host 'API: FAILED - see logs\api.err.log'; exit 1 }
    }

    # ---------- 2. Tunnel (ngrok only, per user decision) ----------
    $tunnelHealthy = $false
    if ($currentUrl -and -not $Force) {
        try {
            if ($currentUrl -like '*ngrok-free.dev*') {
                # ngrok free serves an interstitial page to browsers; the
                # static domain is treated as healthy by URL pattern.
                $tunnelHealthy = $true
            } else {
                $pub = Invoke-WebRequest $currentUrl -UseBasicParsing -Headers @{ 'User-Agent' = 'Mozilla/5.0' } -TimeoutSec 12
                $tunnelHealthy = ($pub.StatusCode -eq 200 -and $pub.Content.Contains('GIFTTRACKER'))
            }
        } catch { $tunnelHealthy = $false }
    }
    if ($ApiOnly) { $tunnelHealthy = $true }

    if ($tunnelHealthy) {
        Write-Host "TUNNEL: healthy - kept $currentUrl"
    } else {
        Write-Host 'TUNNEL: (re)starting...'
        Get-CimInstance Win32_Process | Where-Object {
            ($_.Name -eq 'node.exe' -and $_.CommandLine -like '*tunnelmole*') -or
            ($_.Name -eq 'ngrok.exe') -or
            ($_.Name -eq 'cloudflared.exe') -or
            ($_.Name -eq 'ssh.exe' -and ($_.CommandLine -like '*serveo.net*' -or $_.CommandLine -like '*pinggy*'))
        } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Start-Sleep 1

        Remove-Item "$logs\tunnel.out.log", "$logs\tunnel.err.log" -ErrorAction SilentlyContinue
        $ngrokCmd = "wscript.exe //B scripts\_hidden\tunnel_ngrok.vbs"
        Start-Detached $ngrokCmd $root | Out-Null

        $url = $null
        for ($i = 0; $i -lt 30; $i++) {
            Start-Sleep 1
            try {
                $api = Invoke-RestMethod 'http://127.0.0.1:4040/api/tunnels' -TimeoutSec 2
                $t = @($api.tunnels | Where-Object { $_.proto -eq 'https' })[0]
                if ($t) { $url = $t.public_url; break }
            } catch { }
        }

        if (-not $url) { Write-Host 'TUNNEL: no URL - see logs\tunnel.out.log and logs\ngrok.log'; exit 1 }

        $envText = $envText -replace 'MINI_APP_URL=.*', "MINI_APP_URL=$url/"
        Set-Content $envPath $envText -Encoding UTF8 -NoNewline
        $currentUrl = "$url/"
        Write-Host "TUNNEL: started $currentUrl"
    }
} elseif ($ApiOnly) {
    Write-Host 'TUNNEL: skipped (-ApiOnly)'
} else {
    Write-Host 'API: skipped (-BotOnly)'
    Write-Host "TUNNEL: skipped (-BotOnly) $currentUrl"
}

# ---------- 3. Telegram menu button ----------
try {
    $botToken = [regex]::Match($envText, '(?m)^BOT_TOKEN=(.*)$').Groups[1].Value.Trim()
    if ($botToken -and $botToken -notlike '*PLACEHOLDER*' -and $currentUrl) {
        $menuBody = @{
            menu_button = @{
                type = 'web_app'; text = 'Open App'
                web_app = @{ url = $currentUrl }
            }
        } | ConvertTo-Json -Depth 5
        Invoke-RestMethod "https://api.telegram.org/bot$botToken/setChatMenuButton" `
            -Method Post -ContentType 'application/json' -Body $menuBody -TimeoutSec 10 | Out-Null
        Write-Host 'MENU: mini app button updated'
    }
} catch {
    Write-Host 'MENU: update skipped'
}

# ---------- 3.5 MRKT token service (MTProto, no browser) ----------
if (Test-Path (Join-Path $root 'data\_tg_web_session.json')) {
    $tokenSvc = @(Get-CmdProcs '*mrkt_token_service*')
    if ($tokenSvc.Count -eq 0) {
        Write-Host 'TOKENSVC: starting MRKT token service (MTProto)...'
        $tCmd2 = "wscript.exe //B scripts\_hidden\mrkt_token.vbs"
        Start-Detached $tCmd2 $root | Out-Null
        Start-Sleep 2
    } else {
        Write-Host "TOKENSVC: healthy (pid $($tokenSvc[0].ProcessId)) - kept"
    }
} else {
    Write-Host 'TOKENSVC: skipped (no data\_tg_web_session.json)'
}

# ---------- 4. Bot ----------
$botProcs = @(Get-CmdProcs '*python* -m bot.main*')
$hbPath = Join-Path $root 'data\bot_heartbeat.json'
$hbFresh = $false
$hbUrl = ''
if (Test-Path $hbPath) {
    try {
        $hb = Get-Content $hbPath -Raw | ConvertFrom-Json
        $ts = [datetime]::Parse($hb.timestamp).ToUniversalTime()
        $ageSec = ([datetime]::UtcNow - $ts).TotalSeconds
        $hbFresh = ($ageSec -lt 90 -and $hb.status -eq 'ok')
        $hbUrl = $hb.mini_app_url
    } catch { $hbFresh = $false }
}
$urlMatches = ($hbUrl -and ($hbUrl.TrimEnd('/') -eq $currentUrl.TrimEnd('/')))

if (-not $BotOnly -and $botProcs.Count -eq 1 -and $hbFresh -and $urlMatches -and -not $Force) {
    Write-Host "BOT: healthy (pid $($botProcs[0].ProcessId), heartbeat fresh, url matches) - kept"
} else {
    if ($BotOnly) { Write-Host 'BOT: restarting (-BotOnly)' }
    elseif ($botProcs.Count -gt 1) { Write-Host "BOT: $($botProcs.Count) instances (polling conflict) -> fixing" }
    elseif ($hbFresh -and -not $urlMatches) { Write-Host 'BOT: heartbeat has stale MINI_APP_URL -> restarting' }
    Stop-ByCmd '*python* -m bot.main*'
    Start-Sleep 1
    $botCmd = "wscript.exe //B scripts\_hidden\bot.vbs"
    Start-Detached $botCmd $root | Out-Null
    Start-Sleep 8
    Write-Host 'BOT: started'
    if (Test-Path "$logs\bot.out.log") { Get-Content "$logs\bot.out.log" -Tail 5 }
}

Write-Host ''
Write-Host "OK. Mini App: $currentUrl"
Write-Host 'Healthy stack is left untouched. Use -Force only for a full restart.'
