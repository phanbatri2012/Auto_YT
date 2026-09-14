[CmdletBinding()]
param(
    [ValidateRange(5, 120)]
    [int]$TimeoutSeconds = 20
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = [System.IO.Path]::GetFullPath($PSScriptRoot).TrimEnd("\")
$servicePorts = @(8080, 5173)
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

function Get-ProcessSnapshot {
    return @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
}

function Test-ProjectProcess {
    param($Process)

    if (-not $Process) {
        return $false
    }
    $identity = "$($Process.ExecutablePath)`n$($Process.CommandLine)"
    return $identity.IndexOf(
        $projectRoot,
        [System.StringComparison]::OrdinalIgnoreCase
    ) -ge 0
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
        if (Test-ProjectProcess $current) {
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

function Get-TrustedOmniVoiceWorkerId {
    $markerPath = Join-Path $omniVoiceRoot "data\worker.pid"
    if (-not (Test-Path -LiteralPath $markerPath -PathType Leaf)) {
        return 0
    }
    try {
        $processId = [int](Get-Content -LiteralPath $markerPath -Raw).Trim()
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction Stop
        $commandLine = [string]$process.CommandLine
        if (
            $commandLine -notmatch "uvicorn" -or
            $commandLine -notmatch "api_server:app" -or
            $commandLine -notmatch "--port\s+8011(?:\s|$)"
        ) {
            Write-Warning "OmniVoice PID marker does not identify the expected worker; it will not be stopped."
            return 0
        }
        return $processId
    }
    catch {
        # A stale marker is safe to remove only inside the configured OmniVoice data folder.
        Remove-Item -LiteralPath $markerPath -Force -ErrorAction SilentlyContinue
        return 0
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
    $processes = Get-ProcessSnapshot
    $rootIds = New-Object System.Collections.Generic.HashSet[int]

    foreach ($port in $servicePorts) {
        foreach ($listenerId in Get-ListeningProcessIds $port) {
            $projectAncestorId = Get-ProjectAncestorId ([int]$listenerId) $processes
            if ($projectAncestorId -gt 0) {
                $null = $rootIds.Add($projectAncestorId)
            }
            else {
                Write-Warning "Port $port is used by another application; it will not be stopped."
            }
        }
    }

    foreach ($process in $processes) {
        if (-not (Test-ProjectProcess $process)) {
            continue
        }
        $commandLine = [string]$process.CommandLine
        if (
            $commandLine -match "auto_yt\.main:app" -or
            $commandLine -match "auto_yt\.services\.chatgpt_browser_service" -or
            $commandLine -match "[\\/]vite(?:\.js)?(?:\s|$)" -or
            $commandLine -match "npm(?:\.cmd)?\s+run\s+dev"
        ) {
            $null = $rootIds.Add([int]$process.ProcessId)
        }
    }

    $omniVoiceWorkerId = Get-TrustedOmniVoiceWorkerId
    if ($omniVoiceWorkerId -gt 0) {
        $null = $rootIds.Add($omniVoiceWorkerId)
    }

    if ($rootIds.Count -eq 0) {
        Write-Host "Auto_YT is not running." -ForegroundColor Yellow
        exit 0
    }

    $targetIds = New-Object System.Collections.Generic.HashSet[int]
    foreach ($rootId in $rootIds) {
        foreach ($processId in Get-ProcessTreeIds $rootId $processes) {
            $null = $targetIds.Add($processId)
        }
    }

    Write-Step "Stopping backend, frontend and the trusted OmniVoice worker. Persistent jobs and completed audio chunks remain on disk."
    foreach ($processId in @($targetIds) | Sort-Object -Descending) {
        Stop-Process -Id $processId -ErrorAction SilentlyContinue
    }

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        Start-Sleep -Milliseconds 250
        $remainingProjectListeners = @()
        $snapshot = Get-ProcessSnapshot
        foreach ($port in $servicePorts) {
            foreach ($listenerId in Get-ListeningProcessIds $port) {
                if ((Get-ProjectAncestorId ([int]$listenerId) $snapshot) -gt 0) {
                    $remainingProjectListeners += $listenerId
                }
            }
        }
    } while ($remainingProjectListeners.Count -gt 0 -and (Get-Date) -lt $deadline)

    if ($remainingProjectListeners.Count -gt 0) {
        throw "Auto_YT processes did not stop within $TimeoutSeconds seconds."
    }

    Write-Host "Auto_YT has stopped. Queued jobs were preserved." -ForegroundColor Green
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
