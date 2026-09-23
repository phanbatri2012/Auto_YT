[CmdletBinding()]
param(
    [ValidateRange(5, 120)]
    [int]$TimeoutSeconds = 25
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = [System.IO.Path]::GetFullPath($PSScriptRoot).TrimEnd("\")
$dataRoot = Join-Path $projectRoot "data"
$chromeUserDataRoot = Join-Path $dataRoot "chrome_user_data"
$servicePorts = @(8080, 5173, 8011)
$omniVoiceRoot = if ($env:AUTO_YT_OMNIVOICE_ROOT) {
    [System.IO.Path]::GetFullPath($env:AUTO_YT_OMNIVOICE_ROOT).TrimEnd("\")
}
else {
    [System.IO.Path]::GetFullPath(
        (Join-Path (Split-Path (Split-Path $projectRoot -Parent) -Parent) "omnivoice")
    ).TrimEnd("\")
}

function Write-Step {
    param([string]$Message)
    Write-Host "[Auto_YT] $Message" -ForegroundColor Cyan
}

function Get-RestartMaintenanceStatus {
    $pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
        throw "Cannot verify GPM jobs because the project Python environment is missing."
    }

    $previousPythonPath = $env:PYTHONPATH
    $previousPythonIoEncoding = $env:PYTHONIOENCODING
    try {
        $env:PYTHONPATH = Join-Path $projectRoot "src"
        $env:PYTHONIOENCODING = "utf-8"
        $statusJson = & $pythonPath -m auto_yt.services.maintenance_guard
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($statusJson)) {
            throw "Maintenance guard returned no status."
        }
        return ($statusJson | ConvertFrom-Json)
    }
    catch {
        throw "Cannot verify whether GPM channel jobs are idle: $($_.Exception.Message)"
    }
    finally {
        $env:PYTHONPATH = $previousPythonPath
        $env:PYTHONIOENCODING = $previousPythonIoEncoding
    }
}

function Get-ProcessSnapshot {
    return @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
}

function Test-AutoYTProcess {
    param($Process)

    if (-not $Process) {
        return $false
    }
    $procName = [string]$Process.Name
    if ($procName -in @("chrome.exe", "msedge.exe", "browser.exe", "brave.exe", "firefox.exe", "opera.exe", "explorer.exe")) {
        if ($procName -eq "chrome.exe") {
            return (Test-AutoYTChromeProcess $Process)
        }
        return $false
    }
    $identity = "$($Process.ExecutablePath)`n$($Process.CommandLine)"
    if ($identity.IndexOf($projectRoot, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
        return $true
    }
    if ($omniVoiceRoot -and $identity.IndexOf($omniVoiceRoot, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
        return $true
    }
    return $false
}

function Test-AutoYTChromeProcess {
    param($Process)

    if (-not $Process -or $Process.Name -ne "chrome.exe") {
        return $false
    }
    $cmd = [string]$Process.CommandLine
    if ([string]::IsNullOrWhiteSpace($cmd)) {
        return $false
    }
    if ($cmd.IndexOf($chromeUserDataRoot, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
        return $true
    }
    if ($cmd -match "PROFILE_GPT_" -or $cmd -match "PROFILE_GOOGLE_FLOW_") {
        return $true
    }
    return $false
}

function Get-ProjectAncestorId {
    param(
        [int]$ProcessId,
        [object[]]$Processes
    )

    $byId = @{}
    foreach ($process in $Processes) {
        $byId[[int]$process.ProcessId] = $process
    }

    $currentId = $ProcessId
    $visited = @{}
    while ($currentId -gt 0 -and -not $visited.ContainsKey($currentId)) {
        $visited[$currentId] = $true
        $current = $byId[$currentId]
        if (-not $current) {
            break
        }
        $currName = [string]$current.Name
        if ($currName -in @("explorer.exe", "WindowsTerminal.exe", "conhost.exe", "chrome.exe", "msedge.exe", "browser.exe", "brave.exe", "firefox.exe", "opera.exe")) {
            break
        }
        if (Test-AutoYTProcess $current) {
            return [int]$current.ProcessId
        }
        $currentId = [int]$current.ParentProcessId
    }
    return 0
}

function Get-ProcessTreeIds {
    param(
        [int]$RootId,
        [object[]]$Processes
    )

    $result = New-Object System.Collections.Generic.List[int]
    $pending = New-Object System.Collections.Generic.Queue[int]
    $pending.Enqueue($RootId)
    while ($pending.Count -gt 0) {
        $currentId = $pending.Dequeue()
        if ($result.Contains($currentId)) {
            continue
        }
        $result.Add($currentId)
        foreach ($child in $Processes | Where-Object { [int]$_.ParentProcessId -eq $currentId }) {
            $pending.Enqueue([int]$child.ProcessId)
        }
    }
    return @($result)
}

function Get-ListeningProcessIds {
    param([int]$Port)

    try {
        return @(
            Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop |
                Select-Object -ExpandProperty OwningProcess -Unique
        )
    }
    catch {
        return @()
    }
}

function Request-BrowserServiceStop {
    param([string]$ServiceName)

    $stopFile = Join-Path $dataRoot "$ServiceName.stop.json"
    $stateFile = Join-Path $dataRoot "$ServiceName.json"
    if (Test-Path -LiteralPath $stateFile -PathType Leaf) {
        try {
            $state = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
            $payload = @{
                instance_id = [string]$state.instance_id
                requested_at = (Get-Date).ToUniversalTime().ToString("o")
            } | ConvertTo-Json
            Set-Content -LiteralPath $stopFile -Value $payload -Encoding UTF8
        }
        catch {
            # Non-blocking best effort
        }
    }
}

$createdNew = $false
$stopMutex = New-Object System.Threading.Mutex($true, "Local\AutoYTStop", [ref]$createdNew)
if (-not $createdNew) {
    Write-Host "Another Auto_YT stop is already in progress." -ForegroundColor Yellow
    $stopMutex.Dispose()
    exit 0
}

try {
    Write-Step "Analyzing running Auto_YT services and processes..."

    if ($env:AUTOYT_REQUIRE_GPM_IDLE -eq "1") {
        $maintenanceStatus = Get-RestartMaintenanceStatus
        if ($maintenanceStatus -and -not $maintenanceStatus.safe_to_restart) {
            foreach ($job in @($maintenanceStatus.gpm_blocking_jobs) | Select-Object -First 5) {
                Write-Warning "GPM job is not idle: $($job.status) - $($job.channel_title) - $($job.title)"
            }
            throw "Restart blocked because a queued or active job can open a channel GPM profile. Let the job finish or pause it, then retry."
        }
    }
    
    Request-BrowserServiceStop "chatgpt_browser_service"
    Request-BrowserServiceStop "google_flow_browser_service"

    $processes = Get-ProcessSnapshot
    $rootIds = New-Object System.Collections.Generic.HashSet[int]

    # 1. Listeners on service ports: 8080 (backend), 5173 (frontend), 8011 (omnivoice)
    foreach ($port in $servicePorts) {
        foreach ($listenerId in Get-ListeningProcessIds $port) {
            $ancestorId = Get-ProjectAncestorId ([int]$listenerId) $processes
            if ($ancestorId -gt 0) {
                $null = $rootIds.Add($ancestorId)
            }
            else {
                $proc = $processes | Where-Object { [int]$_.ProcessId -eq [int]$listenerId } | Select-Object -First 1
                if ($proc -and (Test-AutoYTProcess $proc -or [string]$proc.CommandLine -match "api_server:app" -or [string]$proc.CommandLine -match "auto_yt")) {
                    $null = $rootIds.Add([int]$listenerId)
                }
                else {
                    Write-Warning "Port $port is used by an external application (PID: $listenerId); it will not be stopped."
                }
            }
        }
    }

    # 2. Named project processes
    foreach ($process in $processes) {
        $cmd = [string]$process.CommandLine
        $isProject = Test-AutoYTProcess $process

        if ($isProject) {
            if (
                $cmd -match "auto_yt\.main:app" -or
                $cmd -match "auto_yt\.services\.chatgpt_browser_service" -or
                $cmd -match "auto_yt\.services\.google_flow_browser_service" -or
                $cmd -match "api_server:app" -or
                $cmd -match "[\\/]vite(?:\.js)?(?:\s|$)" -or
                $cmd -match "npm(?:\.cmd)?\s+run\s+dev" -or
                $cmd -match "playwright"
            ) {
                $null = $rootIds.Add([int]$process.ProcessId)
            }
        }
        elseif ($cmd -match "api_server:app" -and $cmd -match "8011") {
            $null = $rootIds.Add([int]$process.ProcessId)
        }
    }

    # 3. Dedicated Chrome processes with Auto_YT profiles
    foreach ($process in $processes) {
        if (Test-AutoYTChromeProcess $process) {
            $null = $rootIds.Add([int]$process.ProcessId)
        }
    }

    if ($rootIds.Count -eq 0) {
        Write-Host "Auto_YT is not running." -ForegroundColor Yellow
        exit 0
    }

    $targetIds = New-Object System.Collections.Generic.HashSet[int]
    foreach ($rootId in $rootIds) {
        foreach ($processId in Get-ProcessTreeIds $rootId $processes) {
            $proc = $processes | Where-Object { [int]$_.ProcessId -eq $processId } | Select-Object -First 1
            if ($proc) {
                $procName = [string]$proc.Name
                if ($procName -in @("chrome.exe", "msedge.exe", "browser.exe", "brave.exe", "firefox.exe", "opera.exe", "explorer.exe")) {
                    if ($procName -ne "chrome.exe" -or -not (Test-AutoYTChromeProcess $proc)) {
                        continue
                    }
                }
            }
            $null = $targetIds.Add($processId)
        }
    }

    Write-Step "Stopping backend (8080), frontend (5173), OmniVoice (8011), ChatGPT & Google Flow browser services ($($targetIds.Count) processes)..."
    foreach ($processId in @($targetIds) | Sort-Object -Descending) {
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    }

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        Start-Sleep -Milliseconds 300
        $remainingProjectListeners = @()
        $snapshot = Get-ProcessSnapshot
        foreach ($port in $servicePorts) {
            foreach ($listenerId in Get-ListeningProcessIds $port) {
                $proc = $snapshot | Where-Object { [int]$_.ProcessId -eq [int]$listenerId } | Select-Object -First 1
                if ($proc -and (Test-AutoYTProcess $proc -or [string]$proc.CommandLine -match "api_server:app" -or [string]$proc.CommandLine -match "auto_yt")) {
                    $remainingProjectListeners += $listenerId
                }
            }
        }
    } while ($remainingProjectListeners.Count -gt 0 -and (Get-Date) -lt $deadline)

    if ($remainingProjectListeners.Count -gt 0) {
        foreach ($remId in $remainingProjectListeners) {
            Stop-Process -Id $remId -Force -ErrorAction SilentlyContinue
        }
    }

    Write-Step "Cleaning up state markers and Chrome lock files..."
    $stateFiles = @(
        (Join-Path $dataRoot "chatgpt_browser_service.json"),
        (Join-Path $dataRoot "chatgpt_browser_service.stop.json"),
        (Join-Path $dataRoot "google_flow_browser_service.json"),
        (Join-Path $dataRoot "google_flow_browser_service.stop.json"),
        (Join-Path $omniVoiceRoot "data\worker.pid")
    )
    foreach ($sf in $stateFiles) {
        if (Test-Path -LiteralPath $sf -PathType Leaf) {
            Remove-Item -LiteralPath $sf -Force -ErrorAction SilentlyContinue
        }
    }

    if (Test-Path -LiteralPath $chromeUserDataRoot -PathType Container) {
        Get-ChildItem -LiteralPath $chromeUserDataRoot -Filter "SingletonLock" -Recurse -Force -ErrorAction SilentlyContinue |
            Remove-Item -Force -ErrorAction SilentlyContinue
    }

    Write-Host "[Auto_YT] All services have stopped cleanly. Ports 8080, 5173, and 8011 are free." -ForegroundColor Green
    exit 0
}
catch {
    Write-Host "Auto_YT stop failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
finally {
    $stopMutex.ReleaseMutex()
    $stopMutex.Dispose()
}
