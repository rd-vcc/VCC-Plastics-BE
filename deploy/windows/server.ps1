# VCC Plastics MES - start / stop / status of the servers on a Windows machine, without console windows.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File server.ps1 start    (build UI, start hidden)
#   powershell -NoProfile -ExecutionPolicy Bypass -File server.ps1 stop
#   powershell -NoProfile -ExecutionPolicy Bypass -File server.ps1 status
#
# Layout: <root>\VCC-Plastics-BE (this repo, script in deploy\windows) and <root>\VCC-Plastics-FE
# (the "-main" folder names of a zip download also work). Logs: VCC-Plastics-BE\logs\*.log
# Text is kept ASCII on purpose (Windows PowerShell 5.1 reads scripts in the ANSI code page).

param([ValidateSet('start', 'stop', 'status')][string]$Action = 'status')

$ErrorActionPreference = 'Stop'
$BackendPort = 5005
$FrontendPort = 5173

$Be = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Root = Split-Path $Be -Parent
$Fe = @('VCC-Plastics-FE', 'VCC-Plastics-FE-main') |
    ForEach-Object { Join-Path $Root $_ } |
    Where-Object { Test-Path (Join-Path $_ 'package.json') } |
    Select-Object -First 1
$Logs = Join-Path $Be 'logs'
New-Item -ItemType Directory -Force -Path $Logs | Out-Null

function Get-ListenerPids([int[]]$Ports) {
    Get-NetTCPConnection -State Listen -LocalPort $Ports -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique |
        Where-Object { $_ -gt 4 }   # 0 / 4 = Windows itself, never kill
}

function Test-Port([int]$Port) { [bool](Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) }

function Get-LanUrl {
    $ip = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254*' -and $_.PrefixOrigin -ne 'WellKnown' } |
        Select-Object -First 1 -ExpandProperty IPAddress
    if ($ip) { "http://${ip}:$FrontendPort" } else { "http://localhost:$FrontendPort" }
}

function Stop-Servers {
    $pids = @(Get-ListenerPids @($BackendPort, $FrontendPort))
    if (-not $pids.Count) { Write-Host 'Khong co server nao dang chay.'; return }
    foreach ($p in $pids) {
        $name = (Get-Process -Id $p -ErrorAction SilentlyContinue).ProcessName
        & taskkill.exe /PID $p /T /F | Out-Null
        Write-Host "Da tat tien trinh $p ($name)"
    }
    for ($i = 0; $i -lt 20 -and (Test-Port $BackendPort -or (Test-Port $FrontendPort)); $i++) { Start-Sleep -Milliseconds 500 }
}

# Start a process fully detached from this console (it keeps running after the window closes), no window.
function Start-Hidden([string]$CommandLine, [string]$WorkDir) {
    $startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]0 }
    $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine = $CommandLine; CurrentDirectory = $WorkDir; ProcessStartupInformation = $startup }
    if ($r.ReturnValue -ne 0) { throw "Khong khoi dong duoc tien trinh (ma loi $($r.ReturnValue)): $CommandLine" }
}

function Show-Status {
    $be = Test-Port $BackendPort; $fe = Test-Port $FrontendPort
    Write-Host ("Backend  (cong {0}): {1}" -f $BackendPort, $(if ($be) { 'DANG CHAY' } else { 'DA TAT' }))
    Write-Host ("Giao dien (cong {0}): {1}" -f $FrontendPort, $(if ($fe) { 'DANG CHAY' } else { 'DA TAT' }))
    if ($be -and $fe) { Write-Host "Truy cap: $(Get-LanUrl)" }
}

function Start-Servers {
    $python = Join-Path $Be '.venv\Scripts\python.exe'
    if (-not (Test-Path $python)) { throw "Chua cai moi truong Python: $python (chay: python -m venv .venv  roi  pip install -r requirements.txt)" }
    if (-not $Fe) { throw "Khong tim thay thu muc giao dien VCC-Plastics-FE canh $Be" }
    $node = (Get-Command node -ErrorAction SilentlyContinue).Source
    if (-not $node) { throw 'Chua cai Node.js (khong tim thay lenh node).' }
    $vite = Join-Path $Fe 'node_modules\vite\bin\vite.js'
    if (-not (Test-Path $vite)) { throw "Chua cai thu vien giao dien: chay  npm install  trong $Fe" }

    Stop-Servers | Out-Null

    Write-Host 'Dang build giao dien (khoang 10-30 giay)...'
    $buildLog = Join-Path $Logs 'frontend-build.log'
    $build = Start-Process -FilePath $node -ArgumentList "`"$vite`" build" -WorkingDirectory $Fe -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput $buildLog -RedirectStandardError (Join-Path $Logs 'frontend-build.err.log')
    if ($build.ExitCode -ne 0) { throw "Build giao dien bi loi - xem $buildLog va frontend-build.err.log" }

    Write-Host 'Dang bat backend va giao dien (chay an)...'
    $beLog = Join-Path $Logs 'backend.log'
    $feLog = Join-Path $Logs 'frontend.log'
    Start-Hidden "cmd.exe /c set PYTHONUTF8=1&& `"$python`" -m uvicorn main:app --host 0.0.0.0 --port $BackendPort > `"$beLog`" 2>&1" $Be
    Start-Hidden "cmd.exe /c `"`"$node`" `"$vite`" preview --host 0.0.0.0 --port $FrontendPort --strictPort > `"$feLog`" 2>&1`"" $Fe

    for ($i = 0; $i -lt 60 -and -not ((Test-Port $BackendPort) -and (Test-Port $FrontendPort)); $i++) { Start-Sleep -Milliseconds 500 }
    Show-Status
    if (-not ((Test-Port $BackendPort) -and (Test-Port $FrontendPort))) {
        throw "Server chua len sau 30 giay - xem log trong $Logs"
    }
}

try {
    switch ($Action) {
        'start' { Start-Servers }
        'stop' { Stop-Servers; Show-Status }
        'status' { Show-Status }
    }
} catch {
    Write-Host "LOI: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
