[CmdletBinding()]
param(
    [switch]$NoBrowser,
    [ValidateRange(10, 300)]
    [int]$ReadyTimeoutSeconds = 60
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$projectRoot = [System.IO.Path]::GetFullPath($PSScriptRoot).TrimEnd("\")
$frontendRoot = Join-Path $projectRoot "frontend"
$dataRoot = Join-Path $projectRoot "data"
$logsRoot = Join-Path $dataRoot "logs"
$venvRoot = Join-Path $projectRoot ".venv"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
$backendUrl = "http://127.0.0.1:8080/api/chatgpt-status"
$frontendUrl = "http://127.0.0.1:5173/"

function Write-Step {
    param([string]$Message)
    Write-Host "[Auto_YT] $Message" -ForegroundColor Cyan
}

function Invoke-ExternalCommand {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$FailureMessage
    )

    $commandOutput = @(& $FilePath @Arguments 2>&1)
    $exitCode = $LASTEXITCODE
    foreach ($line in $commandOutput) {
        Write-Host $line
    }
    if ($exitCode -ne 0) {
        throw "$FailureMessage (exit code $exitCode)."
    }
}

function Get-CombinedFingerprint {
    param([string[]]$Paths)

    $hashes = foreach ($path in $Paths) {
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            $stream = [System.IO.File]::Open(
                $path,
                [System.IO.FileMode]::Open,
                [System.IO.FileAccess]::Read,
                [System.IO.FileShare]::ReadWrite
            )
            $sha256 = [System.Security.Cryptography.SHA256]::Create()
            try {
                $hashBytes = $sha256.ComputeHash($stream)
                [System.BitConverter]::ToString($hashBytes).Replace("-", "")
            }
            finally {
                $sha256.Dispose()
                $stream.Dispose()
            }
        }
    }
    return $hashes -join ":"
}

function Test-BackendReady {
    try {
        $null = Invoke-RestMethod -Uri $backendUrl -TimeoutSec 2
        return $true
    }
    catch {
        return $false
    }
}

function Test-FrontendReady {
    try {
        $response = Invoke-WebRequest -Uri $frontendUrl -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -eq 200 -and $response.Content -match "<title>Auto_YT</title>"
    }
    catch {
        return $false
    }
}

function Test-PortInUse {
    param([int]$Port)

    try {
        return [bool](Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop)
    }
    catch {
        $client = New-Object System.Net.Sockets.TcpClient
        try {
            $asyncResult = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
            if (-not $asyncResult.AsyncWaitHandle.WaitOne(300)) {
                return $false
            }
            $client.EndConnect($asyncResult)
            return $true
        }
        catch {
            return $false
        }
        finally {
            $client.Dispose()
        }
    }
}

function Wait-ForService {
    param(
        [string]$Name,
        [scriptblock]$Probe,
        [int]$TimeoutSeconds
    )

    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    while ($timer.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
        if (& $Probe) {
            Write-Step "$Name is ready."
            return
        }
        Start-Sleep -Milliseconds 500
    }
    throw "$Name did not become ready within $TimeoutSeconds seconds."
}

function Test-PythonCommand {
    param(
        [string]$FilePath,
        [string[]]$Prefix = @()
    )

    if (-not (Test-Path -LiteralPath $FilePath -PathType Leaf)) {
        return $false
    }

    try {
        & $FilePath @Prefix -c "import sys; raise SystemExit(0 if sys.version_info.major == 3 else 1)" *> $null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

function Resolve-SystemPython {
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($pythonCommand -and (Test-PythonCommand $pythonCommand.Source)) {
        return @{ FilePath = $pythonCommand.Source; Prefix = @() }
    }

    $pyCommand = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($pyCommand -and (Test-PythonCommand $pyCommand.Source @("-3"))) {
        return @{ FilePath = $pyCommand.Source; Prefix = @("-3") }
    }

    throw "A working Python 3 installation is required but was not found in PATH."
}

function Ensure-PythonEnvironment {
    $venvNeedsRebuild = -not (Test-Path -LiteralPath $venvPython -PathType Leaf)
    $venvConfig = Join-Path $venvRoot "pyvenv.cfg"

    if (-not $venvNeedsRebuild) {
        if (-not (Test-Path -LiteralPath $venvConfig -PathType Leaf)) {
            $venvNeedsRebuild = $true
        }
        else {
            $configText = Get-Content -LiteralPath $venvConfig -Raw
            $venvNeedsRebuild = $configText -notmatch [regex]::Escape($venvRoot)
        }
    }

    if (-not $venvNeedsRebuild -and -not (Test-PythonCommand $venvPython)) {
        $venvNeedsRebuild = $true
    }

    if ($venvNeedsRebuild) {
        if (Test-Path -LiteralPath $venvRoot) {
            $resolvedVenv = (Resolve-Path -LiteralPath $venvRoot).Path.TrimEnd("\")
            if ($resolvedVenv -ne $venvRoot) {
                throw "Refusing to remove unexpected virtual environment path: $resolvedVenv"
            }
            Write-Step "Python environment is missing, moved, or unusable; rebuilding it."
            Remove-Item -LiteralPath $resolvedVenv -Recurse -Force
        }
        else {
            Write-Step "Creating the Python environment."
        }

        $launcher = Resolve-SystemPython
        $arguments = @($launcher.Prefix) + @("-m", "venv", $venvRoot)
        Invoke-ExternalCommand $launcher.FilePath $arguments "Failed to create the Python environment"
    }

    $requirementsPath = Join-Path $projectRoot "requirements.txt"
    if (-not (Test-Path -LiteralPath $requirementsPath -PathType Leaf)) {
        throw "requirements.txt was not found."
    }

    $requirementsFingerprint = Get-CombinedFingerprint @($requirementsPath)
    $requirementsStamp = Join-Path $venvRoot ".autoyt-requirements.sha256"
    $installedFingerprint = if (Test-Path -LiteralPath $requirementsStamp) {
        (Get-Content -LiteralPath $requirementsStamp -Raw).Trim()
    }
    else {
        ""
    }

    $dependenciesHealthy = $false
    if ($installedFingerprint -eq $requirementsFingerprint) {
        & $venvPython -m pip check *> $null
        $dependenciesHealthy = $LASTEXITCODE -eq 0
    }

    if (-not $dependenciesHealthy) {
        Write-Step "Installing Python dependencies."
        Invoke-ExternalCommand $venvPython @(
            "-m", "pip", "install", "--disable-pip-version-check", "-r", $requirementsPath
        ) "Python dependency installation failed"
        Invoke-ExternalCommand $venvPython @("-m", "pip", "check") "Python dependency validation failed"
        Set-Content -LiteralPath $requirementsStamp -Value $requirementsFingerprint -Encoding ASCII -NoNewline
    }
    else {
        Write-Step "Python dependencies are up to date."
    }

    $playwrightVersion = (& $venvPython -c "from importlib.metadata import version; print(version('playwright'))").Trim()
    $installedBrowsers = @(& $venvPython -m playwright install --list 2>&1)
    $browserListExitCode = $LASTEXITCODE
    $browserListText = $installedBrowsers -join "`n"
    $versionPattern = [regex]::Escape($playwrightVersion)
    $currentVersionBlock = @(
        $browserListText -split "Playwright version:\s*" |
            Where-Object { $_ -match "^$versionPattern(?:\r?\n|$)" }
    ) | Select-Object -First 1
    $chromiumReady = $browserListExitCode -eq 0 -and
        $currentVersionBlock -match "(?m)^\s+.*[\\/]chromium-\d+\s*$"

    if (-not $chromiumReady) {
        Write-Step "Installing the Playwright Chromium browser."
        Invoke-ExternalCommand $venvPython @("-m", "playwright", "install", "chromium") "Chromium installation failed"
    }
    else {
        Write-Step "Playwright Chromium is ready."
    }
}

function Ensure-FrontendEnvironment {
    $nodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
    $npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $nodeCommand -or -not $npmCommand) {
        throw "Node.js and npm are required but were not found in PATH."
    }

    $packageJson = Join-Path $frontendRoot "package.json"
    $packageLock = Join-Path $frontendRoot "package-lock.json"
    if (-not (Test-Path -LiteralPath $packageJson -PathType Leaf)) {
        throw "frontend/package.json was not found."
    }

    $manifestPaths = @($packageJson)
    if (Test-Path -LiteralPath $packageLock -PathType Leaf) {
        $manifestPaths += $packageLock
    }
    $manifestFingerprint = Get-CombinedFingerprint $manifestPaths
    $nodeModules = Join-Path $frontendRoot "node_modules"
    $manifestStamp = Join-Path $nodeModules ".autoyt-packages.sha256"
    $installedFingerprint = if (Test-Path -LiteralPath $manifestStamp) {
        (Get-Content -LiteralPath $manifestStamp -Raw).Trim()
    }
    else {
        ""
    }

    if (-not (Test-Path -LiteralPath $nodeModules -PathType Container) -or
        $installedFingerprint -ne $manifestFingerprint) {
        Write-Step "Installing frontend dependencies."
        Push-Location $frontendRoot
        try {
            if (Test-Path -LiteralPath $packageLock -PathType Leaf) {
                Invoke-ExternalCommand $npmCommand.Source @("ci", "--no-audit", "--no-fund") "Frontend dependency installation failed"
            }
            else {
                Invoke-ExternalCommand $npmCommand.Source @("install", "--no-audit", "--no-fund") "Frontend dependency installation failed"
            }
        }
        finally {
            Pop-Location
        }
        Set-Content -LiteralPath $manifestStamp -Value $manifestFingerprint -Encoding ASCII -NoNewline
    }
    else {
        Write-Step "Frontend dependencies are up to date."
    }

    return $npmCommand.Source
}

function Start-Backend {
    New-Item -ItemType Directory -Path $logsRoot -Force | Out-Null
    $env:PYTHONPATH = Join-Path $projectRoot "src"
    Write-Step "Starting backend on port 8080."
    return Start-Process -FilePath $venvPython -ArgumentList @(
        "-m", "uvicorn", "auto_yt.main:app", "--host", "127.0.0.1", "--port", "8080"
    ) -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logsRoot "backend.stdout.log") -RedirectStandardError (Join-Path $logsRoot "backend.stderr.log") -PassThru
}

function Start-Frontend {
    param([string]$NpmPath)

    New-Item -ItemType Directory -Path $logsRoot -Force | Out-Null
    Write-Step "Starting frontend on port 5173."
    return Start-Process -FilePath $NpmPath -ArgumentList @(
        "run", "dev", "--", "--host", "127.0.0.1"
    ) -WorkingDirectory $frontendRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logsRoot "frontend.stdout.log") -RedirectStandardError (Join-Path $logsRoot "frontend.stderr.log") -PassThru
}

$createdNew = $false
$startupMutex = New-Object System.Threading.Mutex($true, "Local\AutoYTStartup", [ref]$createdNew)
if (-not $createdNew) {
    Write-Host "Another Auto_YT startup is already in progress." -ForegroundColor Yellow
    $startupMutex.Dispose()
    exit 0
}

try {
    Set-Location $projectRoot

    if (-not $env:GENMAX_API_KEY) {
        $apiKeyPath = Join-Path $dataRoot "genmax_api_key.txt"
        if (Test-Path -LiteralPath $apiKeyPath -PathType Leaf) {
            $env:GENMAX_API_KEY = (Get-Content -LiteralPath $apiKeyPath -Raw).Trim()
        }
    }
    if (-not $env:GENMAX_API_KEY) {
        Write-Warning "Genmax API key is missing; audio generation will be unavailable."
    }

    $backendReady = Test-BackendReady
    $frontendReady = Test-FrontendReady

    if ($backendReady -and $frontendReady) {
        Write-Step "The full system is already running."
    }
    else {
        if (-not $backendReady) {
            if (Test-PortInUse 8080) {
                Wait-ForService "Backend" ${function:Test-BackendReady} 10
                $backendReady = $true
            }
            else {
                Ensure-PythonEnvironment
                $backendProcess = Start-Backend
                try {
                    Wait-ForService "Backend" ${function:Test-BackendReady} $ReadyTimeoutSeconds
                    $backendReady = $true
                }
                catch {
                    $backendProcess.Refresh()
                    if ($backendProcess.HasExited) {
                        throw "Backend exited during startup. See data/logs/backend.stderr.log."
                    }
                    throw
                }
            }
        }

        if (-not $frontendReady) {
            if (Test-PortInUse 5173) {
                Wait-ForService "Frontend" ${function:Test-FrontendReady} 10
                $frontendReady = $true
            }
            else {
                $npmPath = Ensure-FrontendEnvironment
                $frontendProcess = Start-Frontend $npmPath
                try {
                    Wait-ForService "Frontend" ${function:Test-FrontendReady} $ReadyTimeoutSeconds
                    $frontendReady = $true
                }
                catch {
                    $frontendProcess.Refresh()
                    if ($frontendProcess.HasExited) {
                        throw "Frontend exited during startup. See data/logs/frontend.stderr.log."
                    }
                    throw
                }
            }
        }
    }

    if (-not $backendReady -or -not $frontendReady) {
        throw "The full Auto_YT system is not ready."
    }

    if (-not $NoBrowser) {
        Start-Process $frontendUrl
    }
    Write-Host "Auto_YT is ready: $frontendUrl" -ForegroundColor Green
    exit 0
}
catch {
    Write-Host "Auto_YT startup failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
finally {
    $startupMutex.ReleaseMutex()
    $startupMutex.Dispose()
}
