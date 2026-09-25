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
$backendUrl = "http://127.0.0.1:8080/health"
$legacyBackendUrl = "http://127.0.0.1:8080/api/chatgpt-status"
$frontendUrl = "http://127.0.0.1:5173/"
$omniVoiceUrl = "http://127.0.0.1:8011/health"
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

function Protect-DataDirectory {
    $resolvedDataRoot = [System.IO.Path]::GetFullPath($dataRoot).TrimEnd("\")
    $requiredPrefix = "$projectRoot\"
    if (-not $resolvedDataRoot.StartsWith($requiredPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to change ACL outside the Auto_YT project: $resolvedDataRoot"
    }

    New-Item -ItemType Directory -Path $resolvedDataRoot -Force | Out-Null
    $currentUserSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $grants = @(
        "*$($currentUserSid):(OI)(CI)F",
        "*S-1-5-18:(OI)(CI)F",
        "*S-1-5-32-544:(OI)(CI)F"
    )

    & icacls.exe $resolvedDataRoot "/inheritance:r" "/grant:r" $grants "/remove:g" "*S-1-5-32-545" "*S-1-5-11" "*S-1-1-0" "/Q" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not restrict the data directory ACL."
    }

    $aclMarker = Join-Path $resolvedDataRoot ".acl-protected-v1"
    if (Test-Path -LiteralPath $aclMarker -PathType Leaf) {
        return
    }

    $deferredMarker = Join-Path $resolvedDataRoot ".acl-migration-deferred-v1"
    $currentIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $currentPrincipal = [System.Security.Principal.WindowsPrincipal]::new($currentIdentity)
    $isElevated = $currentPrincipal.IsInRole(
        [System.Security.Principal.WindowsBuiltInRole]::Administrator
    )
    if ((Test-Path -LiteralPath $deferredMarker -PathType Leaf) -and -not $isElevated) {
        Write-Warning "Legacy data ACL migration is pending. Run run_autoyt.bat once as Administrator when Auto_YT Chrome is closed; normal startup will continue."
        return
    }

    $profileProcess = Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine.IndexOf($resolvedDataRoot, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
        } |
        Select-Object -First 1
    if ($profileProcess) {
        Write-Warning "Data ACL migration is waiting for the Auto_YT Chrome profile to close."
        return
    }

    try {
    if (Get-ChildItem -LiteralPath $resolvedDataRoot -Force -ErrorAction SilentlyContinue) {
        $null = & icacls.exe (Join-Path $resolvedDataRoot "*") "/reset" "/T" "/C" "/Q" 2>&1
    }

    $unsafeAccountNames = @(
        ([System.Security.Principal.SecurityIdentifier]::new("S-1-1-0")).Translate([System.Security.Principal.NTAccount]).Value,
        ([System.Security.Principal.SecurityIdentifier]::new("S-1-5-11")).Translate([System.Security.Principal.NTAccount]).Value,
        ([System.Security.Principal.SecurityIdentifier]::new("S-1-5-32-545")).Translate([System.Security.Principal.NTAccount]).Value
    )
    $allItems = @(
        Get-Item -LiteralPath $resolvedDataRoot -Force
        Get-ChildItem -LiteralPath $resolvedDataRoot -Recurse -Force -ErrorAction SilentlyContinue
    )
    $unsafeItems = @($allItems | Where-Object {
        $itemAcl = Get-Acl -LiteralPath $_.FullName
        [bool]($itemAcl.Access | Where-Object {
            $_.AccessControlType -eq [System.Security.AccessControl.AccessControlType]::Allow -and
            $_.IdentityReference.Value -in $unsafeAccountNames
        } | Select-Object -First 1)
    })
    $unsafeDirectories = @($unsafeItems | Where-Object { $_.PSIsContainer })

    foreach ($file in @($unsafeItems | Where-Object { -not $_.PSIsContainer })) {
        $unsafeParent = $unsafeDirectories | Where-Object {
            $directoryPrefix = "$($_.FullName)\"
            $_.FullName -ne $resolvedDataRoot -and
            $file.FullName.StartsWith($directoryPrefix, [System.StringComparison]::OrdinalIgnoreCase)
        } | Select-Object -First 1
        if ($unsafeParent) {
            continue
        }
        $temporaryFile = Join-Path $file.DirectoryName (".acl-migrate-" + [Guid]::NewGuid().ToString("N") + ".tmp")
        try {
            $contentHash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
            Copy-Item -LiteralPath $file.FullName -Destination $temporaryFile
            Move-Item -LiteralPath $temporaryFile -Destination $file.FullName -Force
            if ((Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash -ne $contentHash) {
                throw "Content verification failed after replacing $($file.FullName)."
            }
        }
        catch {
            Write-Warning "Could not migrate the ACL for $($file.FullName): $($_.Exception.Message)"
        }
        finally {
            if (Test-Path -LiteralPath $temporaryFile -PathType Leaf) {
                Remove-Item -LiteralPath $temporaryFile -Force
            }
        }
    }

    $currentUserName = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    foreach ($directory in @($unsafeDirectories | Sort-Object { $_.FullName.Length } -Descending)) {
        if ($directory.FullName -eq $resolvedDataRoot) {
            continue
        }
        if ((Get-Acl -LiteralPath $directory.FullName).Owner -ne $currentUserName) {
            continue
        }
        $parent = Split-Path -Parent $directory.FullName
        $leaf = Split-Path -Leaf $directory.FullName
        $legacyLeaf = ".acl-migrate-" + [Guid]::NewGuid().ToString("N")
        $legacyPath = Join-Path $parent $legacyLeaf
        try {
            Rename-Item -LiteralPath $directory.FullName -NewName $legacyLeaf
            New-Item -ItemType Directory -Path $directory.FullName | Out-Null
            Get-ChildItem -LiteralPath $legacyPath -Force -ErrorAction SilentlyContinue |
                Move-Item -Destination $directory.FullName
            Remove-Item -LiteralPath $legacyPath -Force
        }
        catch {
            Write-Warning "Could not migrate the ACL for $($directory.FullName): $($_.Exception.Message)"
            if (Test-Path -LiteralPath $legacyPath) {
                if (Test-Path -LiteralPath $directory.FullName) {
                    Get-ChildItem -LiteralPath $directory.FullName -Force -ErrorAction SilentlyContinue |
                        Move-Item -Destination $legacyPath -Force
                    Remove-Item -LiteralPath $directory.FullName -Force
                }
                Rename-Item -LiteralPath $legacyPath -NewName $leaf
            }
        }
    }

    $remainingUnsafe = @(
        Get-Item -LiteralPath $resolvedDataRoot -Force
        Get-ChildItem -LiteralPath $resolvedDataRoot -Recurse -Force -ErrorAction SilentlyContinue
    ) | Where-Object {
        $itemAcl = Get-Acl -LiteralPath $_.FullName
        [bool]($itemAcl.Access | Where-Object {
            $_.AccessControlType -eq [System.Security.AccessControl.AccessControlType]::Allow -and
            $_.IdentityReference.Value -in $unsafeAccountNames
        } | Select-Object -First 1)
    } | Select-Object -First 1
    if ($remainingUnsafe) {
        Write-Warning "Some administrator-owned legacy files still have a broad ACL. Run run_autoyt.bat once as Administrator to secure them; normal startup remains available."
        Set-Content -LiteralPath $deferredMarker -Value "Pending administrator migration" -Encoding UTF8
    }
    else {
        Set-Content -LiteralPath $aclMarker -Value "Protected for $currentUserSid" -Encoding UTF8
        Remove-Item -LiteralPath $deferredMarker -Force -ErrorAction SilentlyContinue
    }
    }
    catch {
        Write-Warning "Legacy data ACL migration could not be completed: $($_.Exception.Message) Normal startup will continue."
        try {
            Set-Content -LiteralPath $deferredMarker -Value "Pending administrator migration" -Encoding UTF8
        }
        catch {
            Write-Warning "Could not record the deferred ACL migration: $($_.Exception.Message)"
        }
    }
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
        $health = Invoke-RestMethod -Uri $backendUrl -TimeoutSec 2
        return (
            $health.status -eq "ok" -and
            $null -ne $health.production_coordinator -and
            [bool]$health.production_coordinator.ready
        )
    }
    catch {
        try {
            $null = Invoke-RestMethod -Uri $legacyBackendUrl -TimeoutSec 2
            return $true
        }
        catch {
            $responseProperty = $_.Exception.PSObject.Properties["Response"]
            if ($responseProperty -and $null -ne $responseProperty.Value) {
                $statusCodeProperty = $responseProperty.Value.PSObject.Properties["StatusCode"]
                if ($statusCodeProperty -and [int]$statusCodeProperty.Value -eq 401) {
                    return $true
                }
            }
            return $false
        }
    }
}

function Test-FrontendReady {
    try {
        $response = Invoke-WebRequest -Uri $frontendUrl -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -eq 200 -and (
            $response.Content -match 'id=["'']root["'']' -or
            $response.Content -match 'src=["''].*?main\.(jsx|tsx|js|ts)["'']' -or
            $response.Content -match '(?i)<title>.*?(Auto_YT|Nexus).*?</title>'
        )
    }
    catch {
        return $false
    }
}

function Test-OmniVoiceReady {
    try {
        $response = Invoke-WebRequest -Uri $omniVoiceUrl -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -eq 200
    }
    catch {
        if ($_.Exception -is [System.Net.WebException] -and $_.Exception.Response) {
            $status = [int]$_.Exception.Response.StatusCode
            return ($status -eq 401 -or $status -eq 200)
        }
        return $false
    }
}

function Start-OmniVoiceWorker {
    if (Test-OmniVoiceReady) {
        Write-Step "OmniVoice worker is already running."
        return
    }

    $apiServerPath = Join-Path $omniVoiceRoot "api_server.py"
    if (-not (Test-Path -LiteralPath $apiServerPath -PathType Leaf)) {
        Write-Warning "OmniVoice not found at $omniVoiceRoot; local audio generation will be unavailable."
        return
    }

    Write-Step "Starting OmniVoice TTS worker on port 8011..."
    try {
        $srcPath = Join-Path $projectRoot "src"
        $previousPythonPath = $env:PYTHONPATH
        $env:PYTHONPATH = $srcPath
        try {
            & $venvPython -c "from auto_yt.services.omnivoice_client import ensure_worker_running; ensure_worker_running()" *> $null
        }
        finally {
            $env:PYTHONPATH = $previousPythonPath
        }
    }
    catch {
        Write-Warning "Direct OmniVoice startup returned: $($_.Exception.Message)"
    }
}

function Test-ChatGPTBrowserReady {
    $chatgptStateFile = Join-Path $dataRoot "chatgpt_browser_service.json"
    if (-not (Test-Path -LiteralPath $chatgptStateFile -PathType Leaf)) {
        return $false
    }
    try {
        $data = Get-Content -LiteralPath $chatgptStateFile -Raw | ConvertFrom-Json
        if (-not [bool]$data.ready) {
            return $false
        }
        $cdpUrl = [string]$data.cdp_url
        if ($cdpUrl -match ":(\d+)") {
            $port = [int]$Matches[1]
            return (Test-PortInUse $port)
        }
        return $true
    } catch {
        return $false
    }
}

function Test-GoogleFlowBrowserReady {
    $flowStateFile = Join-Path $dataRoot "google_flow_browser_service.json"
    if (-not (Test-Path -LiteralPath $flowStateFile -PathType Leaf)) {
        return $false
    }
    try {
        $data = Get-Content -LiteralPath $flowStateFile -Raw | ConvertFrom-Json
        if (-not [bool]$data.ready) {
            return $false
        }
        $cdpUrl = [string]$data.cdp_url
        if ($cdpUrl -match ":(\d+)") {
            $port = [int]$Matches[1]
            return (Test-PortInUse $port)
        }
        return $true
    } catch {
        return $false
    }
}

function Test-BrowserServicesReady {
    return @{
        ChatGPT = (Test-ChatGPTBrowserReady)
        GoogleFlow = (Test-GoogleFlowBrowserReady)
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
            $elapsedStr = [math]::Round($timer.Elapsed.TotalSeconds, 1)
            Write-Step "$Name is ready ($elapsedStr s)."
            return
        }
        Start-Sleep -Milliseconds 300
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

    $dependenciesHealthy = ($installedFingerprint -ne "" -and $installedFingerprint -eq $requirementsFingerprint)

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

    $playwrightStamp = Join-Path $venvRoot ".autoyt-playwright.stamp"
    $playwrightHealthy = (Test-Path -LiteralPath $playwrightStamp -PathType Leaf) -and $dependenciesHealthy

    if (-not $playwrightHealthy) {
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
        Set-Content -LiteralPath $playwrightStamp -Value "playwright-$playwrightVersion" -Encoding ASCII -NoNewline
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
    $srcPath = Join-Path $projectRoot "src"
    $stdoutLog = Join-Path $logsRoot "backend.current.stdout.log"
    $stderrLog = Join-Path $logsRoot "backend.current.stderr.log"
    Write-Step "Starting backend on port 8080."
    $previousPythonPath = $env:PYTHONPATH
    try {
        $env:PYTHONPATH = $srcPath
        $process = Start-Process `
            -FilePath $venvPython `
            -ArgumentList @("-m", "uvicorn", "auto_yt.main:app", "--host", "127.0.0.1", "--port", "8080") `
            -WorkingDirectory $projectRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput $stdoutLog `
            -RedirectStandardError $stderrLog `
            -PassThru
    }
    finally {
        if ($null -eq $previousPythonPath) {
            Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
        }
        else {
            $env:PYTHONPATH = $previousPythonPath
        }
    }
    return $process
}

function Reset-ViteCache {
    $viteDir = Join-Path $frontendRoot "node_modules\.vite"
    if (Test-Path -LiteralPath $viteDir) {
        $backupViteName = ".vite_stale_" + [Guid]::NewGuid().ToString("N").Substring(0, 8)
        try {
            Rename-Item -LiteralPath $viteDir -NewName $backupViteName -Force -ErrorAction SilentlyContinue
        }
        catch {
            Remove-Item -LiteralPath $viteDir -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

function Start-Frontend {
    param([string]$NpmPath)

    New-Item -ItemType Directory -Path $logsRoot -Force | Out-Null
    $stdoutLog = Join-Path $logsRoot "frontend.current.stdout.log"
    $stderrLog = Join-Path $logsRoot "frontend.current.stderr.log"
    Write-Step "Starting frontend on port 5173."
    return Start-Process `
        -FilePath $NpmPath `
        -ArgumentList @("run", "dev", "--", "--host", "127.0.0.1") `
        -WorkingDirectory $frontendRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog `
        -PassThru
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

    $apiKeyPath = Join-Path $dataRoot "genmax_api_key.txt"
    $hasStoredApiKey = Test-Path -LiteralPath $apiKeyPath -PathType Leaf
    if (-not $env:GENMAX_API_KEY -and -not $hasStoredApiKey) {
        Write-Warning "Genmax API key is missing; audio generation will be unavailable."
    }

    $backendReady = Test-BackendReady
    $frontendReady = Test-FrontendReady
    $omniVoiceReady = Test-OmniVoiceReady
    $chatgptReady = Test-ChatGPTBrowserReady
    $flowReady = Test-GoogleFlowBrowserReady

    if ($backendReady -and $frontendReady -and $omniVoiceReady -and $chatgptReady -and $flowReady) {
        Write-Step "All core services and browser automations are already running."
    }
    else {
        # --- 1. PARALLEL DISPATCH (All services are launched simultaneously) ---
        if (-not $backendReady) {
            Protect-DataDirectory
            if (Test-PortInUse 8080) {
                Wait-ForService "Backend (8080)" ${function:Test-BackendReady} 10
                $backendReady = $true
            }
            else {
                Ensure-PythonEnvironment
                $backendProcess = Start-Backend
            }
        }

        if (-not $frontendReady) {
            if (-not (Test-PortInUse 5173)) {
                $npmPath = Ensure-FrontendEnvironment
                $frontendProcess = Start-Frontend $npmPath
            }
        }

        if (-not $omniVoiceReady) {
            if (-not (Test-PortInUse 8011)) {
                Start-OmniVoiceWorker
            }
        }

        # --- 2. UI-LAST BARRIER SYNCHRONIZATION ---
        # Wait until ALL 5 services are 100% ready before opening the UI
        Write-Step "Waiting for all 5 services to be ready (Backend, Frontend, OmniVoice, ChatGPT, Google Flow)..."
        $barrierTimer = [System.Diagnostics.Stopwatch]::StartNew()
        $reported = @{}

        while ($barrierTimer.Elapsed.TotalSeconds -lt $ReadyTimeoutSeconds) {
            # --- Early Failure Detection & Vite Self-Healing ---
            if ($frontendProcess -and $frontendProcess.HasExited -and -not $frontendReady) {
                $frontendErrLog = Join-Path $logsRoot "frontend.current.stderr.log"
                $errText = if (Test-Path -LiteralPath $frontendErrLog -PathType Leaf) {
                    Get-Content -LiteralPath $frontendErrLog -Raw -ErrorAction SilentlyContinue
                } else { "" }

                if ($errText -match "EPERM" -and ($errText -match "\.vite" -or $errText -match "unlink")) {
                    Write-Step "Detected locked Vite cache. Self-healing by resetting .vite cache..."
                    Reset-ViteCache
                    Write-Step "Retrying frontend startup with clean cache..."
                    $frontendProcess = Start-Frontend $npmPath
                }
                else {
                    throw "Frontend process exited unexpectedly. See data/logs/frontend.current.stderr.log: $errText"
                }
            }

            if ($backendProcess -and $backendProcess.HasExited -and -not $backendReady) {
                $backendErrLog = Join-Path $logsRoot "backend.current.stderr.log"
                $errText = if (Test-Path -LiteralPath $backendErrLog -PathType Leaf) {
                    Get-Content -LiteralPath $backendErrLog -Raw -ErrorAction SilentlyContinue
                } else { "" }
                throw "Backend process exited during startup. See data/logs/backend.current.stderr.log: $errText"
            }

            if (-not $backendReady -and (Test-BackendReady)) {
                $backendReady = $true
                if (-not $reported.ContainsKey("backend")) {
                    $reported["backend"] = $true
                    Write-Step "Backend API (8080) is ready ($([math]::Round($barrierTimer.Elapsed.TotalSeconds, 1)) s)."
                }
            }
            if (-not $frontendReady -and (Test-FrontendReady)) {
                $frontendReady = $true
                if (-not $reported.ContainsKey("frontend")) {
                    $reported["frontend"] = $true
                    Write-Step "Frontend Web UI (5173) is ready ($([math]::Round($barrierTimer.Elapsed.TotalSeconds, 1)) s)."
                }
            }
            if (-not $omniVoiceReady -and (Test-OmniVoiceReady)) {
                $omniVoiceReady = $true
                if (-not $reported.ContainsKey("omnivoice")) {
                    $reported["omnivoice"] = $true
                    Write-Step "OmniVoice TTS Worker (8011) is ready ($([math]::Round($barrierTimer.Elapsed.TotalSeconds, 1)) s)."
                }
            }
            if (-not $chatgptReady -and (Test-ChatGPTBrowserReady)) {
                $chatgptReady = $true
                if (-not $reported.ContainsKey("chatgpt")) {
                    $reported["chatgpt"] = $true
                    Write-Step "ChatGPT Automation (CDP) is ready ($([math]::Round($barrierTimer.Elapsed.TotalSeconds, 1)) s)."
                }
            }
            if (-not $flowReady -and (Test-GoogleFlowBrowserReady)) {
                $flowReady = $true
                if (-not $reported.ContainsKey("flow")) {
                    $reported["flow"] = $true
                    Write-Step "Google Flow Automation (CDP) is ready ($([math]::Round($barrierTimer.Elapsed.TotalSeconds, 1)) s)."
                }
            }

            if ($backendReady -and $frontendReady -and $omniVoiceReady -and $chatgptReady -and $flowReady) {
                break
            }
            Start-Sleep -Milliseconds 300
        }

        if (-not $backendReady -or -not $frontendReady) {
            throw "The core Auto_YT system did not become ready within $ReadyTimeoutSeconds seconds."
        }
    }

    $finalBrowserState = Test-BrowserServicesReady
    $omniVoiceReady = Test-OmniVoiceReady

    Write-Host ""
    Write-Host "=================================================================" -ForegroundColor Green
    Write-Host "               AUTO_YT SYSTEM IS READY TO PRODUCE VIDEOS          " -ForegroundColor Green
    Write-Host "=================================================================" -ForegroundColor Green
    Write-Host " [OK] Backend API Server:     $backendUrl" -ForegroundColor Green
    Write-Host " [OK] Production Coordinator: Ready (render + YouTube publish)" -ForegroundColor Green
    Write-Host " [OK] Frontend Web UI:        $frontendUrl" -ForegroundColor Green
    if ($omniVoiceReady) {
        Write-Host " [OK] OmniVoice TTS Server:   $omniVoiceUrl" -ForegroundColor Green
    } else {
        Write-Host " [!]  OmniVoice TTS Server:   Offline (remote voice fallback)" -ForegroundColor Yellow
    }
    if ($finalBrowserState.ChatGPT) {
        Write-Host " [OK] ChatGPT Automation:     Ready (Chromium CDP connected)" -ForegroundColor Green
    } else {
        Write-Host " [!]  ChatGPT Automation:     Connecting in background" -ForegroundColor Yellow
    }
    if ($finalBrowserState.GoogleFlow) {
        Write-Host " [OK] Google Flow Automation: Ready (Chromium CDP connected)" -ForegroundColor Green
    } else {
        Write-Host " [!]  Google Flow Automation: Connecting in background" -ForegroundColor Yellow
    }
    Write-Host "=================================================================" -ForegroundColor Green
    Write-Host ""

    # UI OPENS LAST: Only after all services have completed synchronization
    if (-not $NoBrowser) {
        Start-Process $frontendUrl
    }
    Write-Host "Auto_YT interface is open at: $frontendUrl" -ForegroundColor Green
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
