param(
    [string]$BuildStep = 'package-rdst-desktop-windows-store',
    [string]$ExpectedSignaturePublisher = 'READYSET TECHNOLOGY, INC.',
    [string]$ExpectedInstalledPublisher = 'Readyset',
    [switch]$FunctionsOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList
    )

    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath $($ArgumentList -join ' ') failed with exit code $LASTEXITCODE"
    }
}

function Stop-ProcessTree {
    param([Parameter(Mandatory = $true)][int]$ProcessId)

    $taskkill = Start-Process -FilePath 'taskkill.exe' `
        -ArgumentList @('/PID', "$ProcessId", '/T', '/F') -PassThru
    if (-not $taskkill.WaitForExit(10000)) {
        $taskkill.Kill()
        throw "taskkill.exe timed out while stopping process $ProcessId"
    }
    $taskkill.WaitForExit()
    if (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue) {
        throw "Process tree remained after taskkill.exe: $ProcessId"
    }
}

function Invoke-BoundedProcess {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds,
        [string]$StandardOutputPath,
        [string]$StandardErrorPath
    )

    # Start-Process joins arguments with spaces and quotes none of them.
    $quotedArguments = @($ArgumentList | ForEach-Object {
        if ($_ -match '\s' -and $_ -notmatch '^".*"$') { '"' + $_ + '"' } else { $_ }
    })
    $startParameters = @{
        FilePath = $FilePath
        ArgumentList = $quotedArguments
        PassThru = $true
    }
    if ($StandardOutputPath) {
        $startParameters['RedirectStandardOutput'] = $StandardOutputPath
    }
    if ($StandardErrorPath) {
        $startParameters['RedirectStandardError'] = $StandardErrorPath
    }
    $process = Start-Process @startParameters
    # Read the handle now so a redirected process still reports its ExitCode.
    $null = $process.Handle
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        Stop-ProcessTree $process.Id
        throw "$FilePath timed out after ${TimeoutSeconds} seconds"
    }
    $process.WaitForExit()
    return $process.ExitCode
}

function Invoke-CapturedChecked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [Parameter(Mandatory = $true)][string]$OutputPath,
        [int]$TimeoutSeconds = 600
    )

    $stdoutPath = "$OutputPath.stdout.tmp"
    $stderrPath = "$OutputPath.stderr.tmp"
    try {
        $exitCode = Invoke-BoundedProcess $FilePath $ArgumentList $TimeoutSeconds `
            $stdoutPath $stderrPath
        $output = @(
            Get-Content $stdoutPath, $stderrPath -ErrorAction SilentlyContinue |
                ForEach-Object { "$_" }
        )
        [System.IO.File]::WriteAllLines(
            $OutputPath,
            $output,
            [System.Text.UTF8Encoding]::new($false)
        )
        if ($exitCode -ne 0) {
            throw "$FilePath $($ArgumentList -join ' ') failed with exit code $exitCode"
        }
    } finally {
        Remove-Item $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue
    }
}

function Require-Command {
    param([Parameter(Mandatory = $true)][string]$Name)

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command not found: $Name"
    }
}

function Require-TimestampedSignature {
    param([Parameter(Mandatory = $true)][string]$Path)

    $signature = Get-AuthenticodeSignature -FilePath $Path
    if ($signature.Status -ne 'Valid') {
        throw "Invalid Authenticode signature for ${Path}: $($signature.Status) $($signature.StatusMessage)"
    }
    if (-not $signature.TimeStamperCertificate) {
        throw "Authenticode signature has no trusted timestamp: $Path"
    }
    return $signature
}

function Require-FirstPartySignature {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$ExpectedPublisher
    )

    $signature = Require-TimestampedSignature $Path
    $actualPublisher = if ($signature.SignerCertificate) {
        $signature.SignerCertificate.GetNameInfo(
            [Security.Cryptography.X509Certificates.X509NameType]::SimpleName,
            $false
        )
    } else {
        '<missing>'
    }
    if ($actualPublisher -ne $ExpectedPublisher) {
        throw "Unexpected Authenticode publisher for ${Path}: $actualPublisher"
    }
}

function ConvertTo-SignatureEvidence {
    param(
        [Parameter(Mandatory = $true)][System.IO.FileInfo]$File,
        [Parameter(Mandatory = $true)][string]$Root
    )

    $signature = Require-TimestampedSignature $File.FullName
    $relativePath = $File.FullName.Substring($Root.Length).TrimStart('\', '/').Replace('\', '/')
    return [ordered]@{
        path = $relativePath
        sha256 = (Get-FileHash -Algorithm SHA256 -Path $File.FullName).Hash.ToLowerInvariant()
        signer = $signature.SignerCertificate.Subject
        signerThumbprint = $signature.SignerCertificate.Thumbprint
        timestamp = $signature.TimeStamperCertificate.Subject
        timestampThumbprint = $signature.TimeStamperCertificate.Thumbprint
    }
}

# A JSON array as an array. Windows PowerShell 5.1's ConvertFrom-Json writes an array to the
# pipeline as one object, so @(... | ConvertFrom-Json) would hold a single nested array.
function Read-JsonArray {
    param([Parameter(Mandatory = $true)][string]$Path)

    $parsed = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    return ,@($parsed)
}

function Get-RdstProtocolKeys {
    return @(
        'HKCU:\Software\Classes\rdst',
        'HKLM:\Software\Classes\rdst'
    ) | Where-Object { Test-Path $_ }
}

function Get-RdstUninstallEntries {
    $registryPaths = @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
    )
    return @(Get-ItemProperty $registryPaths -ErrorAction SilentlyContinue | Where-Object {
        $displayName = $_.PSObject.Properties['DisplayName']
        $displayName -and $displayName.Value -eq 'RDST Desktop'
    })
}

function Get-RdstStartMenuShortcuts {
    $roots = @(
        [Environment]::GetFolderPath('CommonPrograms'),
        [Environment]::GetFolderPath('Programs')
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) -and (Test-Path $_) }
    return @($roots | ForEach-Object {
        Get-ChildItem $_ -Recurse -File -Filter 'RDST Desktop.lnk' -ErrorAction SilentlyContinue
    })
}

function Get-RdstDesktopShortcuts {
    $roots = @(
        [Environment]::GetFolderPath('CommonDesktopDirectory'),
        [Environment]::GetFolderPath('DesktopDirectory')
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) -and (Test-Path $_) }
    return @($roots | ForEach-Object {
        Get-ChildItem $_ -File -Filter 'RDST Desktop.lnk' -ErrorAction SilentlyContinue
    })
}

# RDST data in the agent's own profile: a per-user install, the app's Electron profile (named
# after the product or the package), the updater's download cache and the backend's data
# directory. Known folders ignore the smoke run's redirected APPDATA and USERPROFILE, so these
# are the agent's real ones.
function Get-RdstProfilePaths {
    $roaming = [Environment]::GetFolderPath('ApplicationData')
    $local = [Environment]::GetFolderPath('LocalApplicationData')
    $userProfile = [Environment]::GetFolderPath('UserProfile')
    return @(
        (Join-Path $roaming 'RDST Desktop'),
        (Join-Path $roaming 'rdst-desktop'),
        (Join-Path $local 'rdst-desktop-updater'),
        (Join-Path $local 'Programs\RDST Desktop'),
        (Join-Path $userProfile '.rdst')
    ) | Where-Object { Test-Path $_ }
}

# Credential Manager entries the backend writes (its sign-in, keys and Slack token), from
# `cmdkey /list` output.
function Get-RdstCredentialTargets {
    param([string[]]$Listing = @(cmdkey.exe /list))

    return @($Listing | ForEach-Object {
        if ($_ -match '^\s*Target:\s*(?:LegacyGeneric:target=)?(.+?)\s*$') { $Matches[1] }
    } | Where-Object { $_ -match '(^|@)rdst-(web|slack)(-[^@]*)?$' })
}

# Removes everything an interrupted certification run installed. Only called when that run's
# marker shows the RDST state on this agent is the certification's own.
function Clear-CertificationInstall {
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [Parameter(Mandatory = $true)][string]$Uninstaller
    )

    if (Test-Path -LiteralPath $Uninstaller -PathType Leaf) {
        try {
            Invoke-RdstUninstall $Uninstaller $InstallRoot
        } catch {
            Write-Warning "Uninstalling the interrupted run's RDST Desktop failed: $($_.Exception.Message)"
        }
    }
    foreach ($problem in @(Remove-RdstPartialState $InstallRoot)) {
        Write-Warning $problem
    }
    Get-RdstProfilePaths | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    foreach ($target in @(Get-RdstCredentialTargets)) {
        & cmdkey.exe "/delete:$target" | Out-Null
    }
}

function Get-RdstProcesses {
    param([Parameter(Mandatory = $true)][string]$InstallRoot)

    return @(Get-CimInstance Win32_Process | Where-Object {
        $_.ExecutablePath -and $_.ExecutablePath -like "${InstallRoot}*"
    })
}

function Get-RdstServices {
    param([Parameter(Mandatory = $true)][string]$InstallRoot)

    return @(Get-CimInstance Win32_Service | Where-Object {
        $_.PathName -and $_.PathName -like "*${InstallRoot}*"
    })
}

function Get-RdstScheduledTasks {
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [object[]]$Tasks = @(Get-ScheduledTask)
    )

    return @($Tasks | Where-Object {
        @($_.Actions | Where-Object {
            $execute = $_.PSObject.Properties['Execute']
            $execute -and $execute.Value -like "*${InstallRoot}*"
        }).Count -gt 0
    })
}

function Get-RdstStateFindings {
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [string]$AllowedWorkDirectory
    )

    $findings = @()
    if (Test-Path $InstallRoot) {
        $findings += 'installation directory'
    }
    if (@(Get-RdstProtocolKeys).Count -gt 0) {
        $findings += 'protocol registration'
    }
    if (@(Get-RdstUninstallEntries).Count -gt 0) {
        $findings += 'Add/Remove Programs entry'
    }
    if (@(Get-RdstStartMenuShortcuts).Count -gt 0 -or
        @(Get-RdstDesktopShortcuts).Count -gt 0) {
        $findings += 'shortcut'
    }
    $profilePaths = @(Get-RdstProfilePaths)
    if ($profilePaths.Count -gt 0) {
        $findings += "RDST data in the agent profile ($($profilePaths -join ', '))"
    }
    if (@(Get-RdstCredentialTargets).Count -gt 0) {
        $findings += 'Credential Manager entry'
    }
    if (@(Get-RdstProcesses $InstallRoot).Count -gt 0) {
        $findings += 'process'
    }
    if (@(Get-RdstServices $InstallRoot).Count -gt 0) {
        $findings += 'service'
    }
    if (@(Get-RdstScheduledTasks $InstallRoot).Count -gt 0) {
        $findings += 'scheduled task'
    }
    if (@(Get-RdstFirewallRules).Count -gt 0) {
        $findings += 'certification firewall rule'
    }
    $workDirectories = @(Get-ChildItem ([IO.Path]::GetTempPath()) -Directory `
        -Filter 'rdst-store-cert-*' -ErrorAction SilentlyContinue | Where-Object {
            -not $AllowedWorkDirectory -or $_.FullName -ne $AllowedWorkDirectory
        })
    if ($workDirectories.Count -gt 0) {
        $findings += 'certification work directory'
    }
    return $findings
}

function Get-DefenderCommand {
    $platformRoot = Join-Path $env:ProgramData 'Microsoft\Windows Defender\Platform'
    $platformCommands = @(if (Test-Path $platformRoot) {
        Get-ChildItem $platformRoot -Recurse -File -Filter 'MpCmdRun.exe' |
            Sort-Object LastWriteTimeUtc -Descending
    })
    if ($platformCommands.Count -gt 0) {
        return $platformCommands[0].FullName
    }

    $fallback = Join-Path $env:ProgramFiles 'Windows Defender\MpCmdRun.exe'
    if (Test-Path $fallback -PathType Leaf) {
        return $fallback
    }
    throw 'Microsoft Defender command-line scanner is unavailable'
}

function Get-RdstFirewallRules {
    return @(Get-NetFirewallRule -Group 'RDST Store Certification' -ErrorAction SilentlyContinue)
}

function Enable-NetworkIsolation {
    param(
        [Parameter(Mandatory = $true)][hashtable]$State,
        [Parameter(Mandatory = $true)][string[]]$Programs
    )

    if (@(Get-RdstFirewallRules).Count -gt 0) {
        throw 'Stale RDST Store certification firewall rules are present'
    }
    foreach ($program in $Programs) {
        $displayName = "RDST Store outbound block-$([guid]::NewGuid().ToString('N'))"
        $createdRule = New-NetFirewallRule -DisplayName $displayName `
            -Group 'RDST Store Certification' -Direction Outbound -Action Block `
            -Program $program -Profile Any
        $applicationFilter = $createdRule | Get-NetFirewallApplicationFilter
        if ("$($createdRule.Enabled)" -ne 'True' -or
            "$($createdRule.Direction)" -ne 'Outbound' -or
            "$($createdRule.Action)" -ne 'Block' -or
            "$($applicationFilter.Program)" -ne $program) {
            throw "Could not isolate outbound traffic for $program"
        }
        $State.rules += $createdRule.Name
        $State.programs += $program
    }
    $State.enabled = $true
}

function Disable-NetworkIsolation {
    param([Parameter(Mandatory = $true)][hashtable]$State)

    $errors = @()
    foreach ($name in $State.rules) {
        try {
            Remove-NetFirewallRule -Name $name -ErrorAction Stop
            if (Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue) {
                $errors += "Firewall rule remained after removal: $name"
            }
        } catch {
            $errors += "Could not remove firewall rule ${name}: $($_.Exception.Message)"
        }
    }
    $State.enabled = $errors.Count -gt 0
    return $errors
}

function Wait-ForRemoval {
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [int]$TimeoutSeconds = 120
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $installed = Test-Path $InstallRoot
        $registered = @(Get-RdstUninstallEntries).Count -gt 0
        if (-not $installed -and -not $registered) {
            return
        }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "RDST Desktop remained installed after ${TimeoutSeconds} seconds"
}

function Invoke-RdstUninstall {
    param(
        [Parameter(Mandatory = $true)][string]$Uninstaller,
        [Parameter(Mandatory = $true)][string]$InstallRoot
    )

    if (-not (Test-Path $Uninstaller -PathType Leaf)) {
        throw "RDST Desktop uninstaller is unavailable: $Uninstaller"
    }
    $exitCode = Invoke-BoundedProcess $Uninstaller @('/S') 120
    Wait-ForRemoval -InstallRoot $InstallRoot
    if ($exitCode -ne 0) {
        throw "RDST Desktop uninstaller exited with code $exitCode"
    }
    if (@(Get-RdstStartMenuShortcuts).Count -gt 0 -or
        @(Get-RdstDesktopShortcuts).Count -gt 0) {
        throw 'RDST Desktop shortcuts remained after uninstall'
    }
    if (@(Get-RdstProtocolKeys).Count -gt 0) {
        throw 'RDST protocol registration remained after uninstall'
    }
}

function Remove-RdstPartialState {
    param([Parameter(Mandatory = $true)][string]$InstallRoot)

    $errors = @()
    try {
        Get-RdstProcesses $InstallRoot | ForEach-Object {
            Stop-ProcessTree $_.ProcessId
        }
    } catch {
        $errors += "Could not stop RDST Desktop processes: $($_.Exception.Message)"
    }
    try {
        $protocolKeys = @(Get-RdstProtocolKeys)
    } catch {
        $protocolKeys = @()
        $errors += "Could not list RDST protocol registrations: $($_.Exception.Message)"
    }
    foreach ($protocolKey in $protocolKeys) {
        try {
            Remove-Item $protocolKey -Recurse -Force -ErrorAction Stop
        } catch {
            $errors += "Could not remove RDST protocol registration: $($_.Exception.Message)"
        }
    }
    try {
        $uninstallEntries = @(Get-RdstUninstallEntries)
    } catch {
        $uninstallEntries = @()
        $errors += "Could not list RDST Desktop uninstall entries: $($_.Exception.Message)"
    }
    foreach ($entry in $uninstallEntries) {
        try {
            Remove-Item $entry.PSPath -Recurse -Force -ErrorAction Stop
        } catch {
            $errors += "Could not remove RDST Desktop uninstall entry: $($_.Exception.Message)"
        }
    }
    try {
        $shortcuts = @(Get-RdstStartMenuShortcuts) + @(Get-RdstDesktopShortcuts)
    } catch {
        $shortcuts = @()
        $errors += "Could not list RDST Desktop shortcuts: $($_.Exception.Message)"
    }
    foreach ($shortcut in $shortcuts) {
        try {
            Remove-Item $shortcut.FullName -Force -ErrorAction Stop
        } catch {
            $errors += "Could not remove RDST Desktop shortcut: $($_.Exception.Message)"
        }
    }
    try {
        $services = @(Get-RdstServices $InstallRoot)
    } catch {
        $services = @()
        $errors += "Could not list RDST Desktop services: $($_.Exception.Message)"
    }
    foreach ($service in $services) {
        try {
            $exitCode = Invoke-BoundedProcess 'sc.exe' @('delete', $service.Name) 30
            if ($exitCode -ne 0) {
                throw "sc.exe exited with code $exitCode"
            }
        } catch {
            $errors += "Could not remove RDST Desktop service $($service.Name): $($_.Exception.Message)"
        }
    }
    try {
        $scheduledTasks = @(Get-RdstScheduledTasks $InstallRoot)
    } catch {
        $scheduledTasks = @()
        $errors += "Could not list RDST Desktop scheduled tasks: $($_.Exception.Message)"
    }
    foreach ($task in $scheduledTasks) {
        try {
            Unregister-ScheduledTask -TaskName $task.TaskName -TaskPath $task.TaskPath `
                -Confirm:$false -ErrorAction Stop
        } catch {
            $errors += "Could not remove RDST Desktop scheduled task $($task.TaskName): $($_.Exception.Message)"
        }
    }
    if (Test-Path $InstallRoot) {
        try {
            Remove-Item $InstallRoot -Recurse -Force -ErrorAction Stop
        } catch {
            $errors += "Could not remove partial RDST Desktop installation: $($_.Exception.Message)"
        }
    }
    return $errors
}

function Write-Json {
    param(
        [Parameter(Mandatory = $true)]$Value,
        [Parameter(Mandatory = $true)][string]$Path
    )

    $Value | ConvertTo-Json -Depth 10 | Set-Content $Path -Encoding UTF8
}

if ($FunctionsOnly) {
    return
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path
$artifactRelativeDir = 'rdst/.buildkite-artifacts/rdst-desktop-windows-store'
$certificationRelativeDir = 'rdst/.buildkite-artifacts/rdst-desktop-windows-store-certification'
$certificationDir = Join-Path $repoRoot $certificationRelativeDir
$workDir = Join-Path ([IO.Path]::GetTempPath()) "rdst-store-cert-$([guid]::NewGuid().ToString('N'))"
$packagesDir = Join-Path $workDir 'packages'
$stateDir = Join-Path $workDir 'state'
$installRoot = Join-Path $env:ProgramFiles 'RDST Desktop'
$installedApp = Join-Path $installRoot 'RDST Desktop.exe'
$sidecar = Join-Path $installRoot 'sidecar\rdst\rdst.exe'
$uninstaller = Join-Path $installRoot 'Uninstall RDST Desktop.exe'
# Written before installing and removed once cleanup verified nothing is left: if it is here at
# startup, a run was killed with its install in place, and that install is this script's own.
$certificationMarker = Join-Path $env:ProgramData 'RDST Store Certification\installed'
$networkIsolation = @{
    enabled = $false
    programs = @()
    rules = @()
}
$certificationFailure = $null
$cleanupFailures = @()
$installerHash = $null
$version = $null
$checks = [ordered]@{
    artifactManifest = 'not_run'
    authenticode = 'not_run'
    defender = 'not_run'
    networkIsolation = 'not_run'
    machineInstall = 'not_run'
    launch = 'not_run'
    privacyReset = 'not_run'
    silentUninstall = 'not_run'
    cleanup = 'not_run'
}
$smokeEnvironmentNames = @(
    'APPDATA',
    'RDST_DESKTOP_CERTIFICATION',
    'RDST_DESKTOP_SMOKE',
    'RDST_DESKTOP_SMOKE_MARKER',
    'RDST_TELEMETRY',
    'TEMP',
    'TMP',
    'USERPROFILE'
)
$originalSmokeEnvironment = @{}
# Cleanup removes only what this run installed: RDST state found before it is left alone.
$preflightPassed = $false
foreach ($name in $smokeEnvironmentNames) {
    $originalSmokeEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}

Remove-Item $certificationDir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $packagesDir, $stateDir, $certificationDir | Out-Null

try {
    foreach ($command in @(
        'buildkite-agent',
        'Get-CimInstance',
        'Get-NetFirewallApplicationFilter',
        'Get-NetFirewallRule',
        'Get-ScheduledTask',
        'New-NetFirewallRule',
        'Remove-NetFirewallRule',
        'Unregister-ScheduledTask'
    )) {
        Require-Command $command
    }
    $principal = [Security.Principal.WindowsPrincipal]::new(
        [Security.Principal.WindowsIdentity]::GetCurrent()
    )
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Store certification must run from an elevated Windows agent'
    }
    if ($env:BUILDKITE_BRANCH -notin @('main', 'refs/heads/main')) {
        throw "Store certification must run from main; branch=$($env:BUILDKITE_BRANCH)"
    }

    foreach ($pattern in @(
        "$artifactRelativeDir/*",
        "$artifactRelativeDir/evidence/*"
    )) {
        Invoke-Checked 'buildkite-agent' @(
            'artifact', 'download', $pattern, $packagesDir, '--step', $BuildStep
        )
    }

    $manifests = @(Get-ChildItem $packagesDir -Recurse -File -Filter 'artifacts.json')
    if ($manifests.Count -ne 1) {
        throw "Expected one Store artifact manifest; found $($manifests.Count)"
    }
    $artifactRoot = $manifests[0].Directory.FullName
    $manifestEntries = Read-JsonArray $manifests[0].FullName
    if ($manifestEntries.Count -eq 0) {
        throw 'Store artifact manifest is empty'
    }
    $artifactRootFull = [IO.Path]::GetFullPath($artifactRoot).TrimEnd('\', '/')
    foreach ($entry in $manifestEntries) {
        $entryPath = "$($entry.path)"
        $fullPath = [IO.Path]::GetFullPath((Join-Path $artifactRoot $entryPath))
        if (-not $fullPath.StartsWith("$artifactRootFull\", [StringComparison]::OrdinalIgnoreCase)) {
            throw "Artifact manifest path escapes its root: $entryPath"
        }
        if (-not (Test-Path $fullPath -PathType Leaf)) {
            throw "Artifact manifest references a missing file: $entryPath"
        }
        $actualHash = (Get-FileHash -Algorithm SHA256 -Path $fullPath).Hash.ToLowerInvariant()
        if ($actualHash -ne "$($entry.sha256)".ToLowerInvariant()) {
            throw "Artifact hash mismatch: $entryPath"
        }
        if ((Get-Item $fullPath).Length -ne [long]$entry.size) {
            throw "Artifact size mismatch: $entryPath"
        }
    }

    $installers = @(Get-ChildItem $artifactRoot -File -Filter '*-store.exe')
    if ($installers.Count -ne 1) {
        throw "Expected one Store installer; found $($installers.Count)"
    }
    $installer = $installers[0]
    $installerHash = (Get-FileHash -Algorithm SHA256 -Path $installer.FullName).Hash.ToLowerInvariant()

    $checksumPath = Join-Path $artifactRoot 'SHA256SUMS'
    $checksumLines = @(Get-Content $checksumPath | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($checksumLines.Count -ne 1 -or
        $checksumLines[0] -ne "$installerHash  $($installer.Name)") {
        throw 'Store SHA256SUMS must contain only the exact installer hash'
    }

    $updaterFiles = @(Get-ChildItem $artifactRoot -Recurse -File | Where-Object {
        $_.Name -like 'latest*.yml' -or $_.Extension -eq '.blockmap'
    } | ForEach-Object { $_.FullName.Substring($artifactRoot.Length).TrimStart('\', '/') })
    if (($updaterFiles -join ',') -ne 'update\latest.yml') {
        throw "Store artifacts must carry only the Store channel's latest.yml: $($updaterFiles -join ', ')"
    }

    $provenancePath = Join-Path $artifactRoot 'evidence\provenance.json'
    $provenance = Get-Content $provenancePath -Raw | ConvertFrom-Json
    $version = "$($provenance.version)"
    if ($provenance.distribution -ne 'microsoft-store' -or
        $provenance.checks.authenticode -ne 'passed' -or
        $provenance.checks.inAppUpdates -ne 'enabled' -or
        "$($provenance.checks.updateFeed)" -notlike '*/windows-store/update' -or
        $provenance.checks.hostedReportUpload -ne 'disabled') {
        throw 'Store build provenance does not contain the required build checks'
    }
    $checks.artifactManifest = 'passed'

    # Leftovers of an earlier certification run belong to this script, not to RDST: clear them so
    # one interrupted run doesn't fail every later one on this agent.
    Get-RdstFirewallRules | Remove-NetFirewallRule -ErrorAction SilentlyContinue
    Get-ChildItem ([IO.Path]::GetTempPath()) -Directory -Filter 'rdst-store-cert-*' `
        -ErrorAction SilentlyContinue | Where-Object { $_.FullName -ne $workDir } |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    # Packaging used to run the sidecar's --version check against this profile, which left a data
    # directory that is empty or holds only an empty keyring lock: no RDST data, so clear exactly that.
    $profileDataDir = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.rdst'
    if (Test-Path -LiteralPath $profileDataDir -PathType Container) {
        $profileDataItems = @(Get-ChildItem -LiteralPath $profileDataDir -Force -Recurse)
        $onlyEmptyLock = $profileDataItems.Count -eq 1 -and $profileDataItems[0].Name -eq '.keyring.lock' -and
            -not $profileDataItems[0].PSIsContainer -and $profileDataItems[0].Length -eq 0
        if ($profileDataItems.Count -eq 0 -or $onlyEmptyLock) {
            Write-Host "Removing the empty RDST data directory an earlier package build left: $profileDataDir"
            Remove-Item -LiteralPath $profileDataDir -Recurse -Force
        }
    }
    if (Test-Path -LiteralPath $certificationMarker -PathType Leaf) {
        Write-Host 'An earlier certification run was interrupted; removing its installation'
        Clear-CertificationInstall $installRoot $uninstaller
        if (@(Get-RdstStateFindings $installRoot $workDir).Count -eq 0) {
            Remove-Item -LiteralPath $certificationMarker -Force
        }
    }
    $preflightFindings = @(Get-RdstStateFindings $installRoot $workDir)
    if ($preflightFindings.Count -gt 0) {
        throw "Certification agent contains pre-existing RDST state: $($preflightFindings -join ', ')"
    }
    $preflightPassed = $true
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $certificationMarker) | Out-Null
    Set-Content -LiteralPath $certificationMarker -Value $env:BUILDKITE_JOB_ID -Encoding Ascii

    $defender = Get-DefenderCommand
    Invoke-CapturedChecked $defender @('-SignatureUpdate') (
        Join-Path $certificationDir 'defender-signature-update.log'
    )
    Require-FirstPartySignature $installer.FullName $ExpectedSignaturePublisher
    Invoke-CapturedChecked $defender @(
        '-Scan', '-ScanType', '3', '-File', $installer.FullName, '-DisableRemediation'
    ) (Join-Path $certificationDir 'defender-installer.log')

    Enable-NetworkIsolation $networkIsolation @(
        $installer.FullName,
        $installedApp,
        $sidecar
    )
    $checks.networkIsolation = 'passed'
    $installExitCode = Invoke-BoundedProcess $installer.FullName @('/S') 300
    if ($installExitCode -ne 0) {
        throw "Silent Store installation failed with exit code $installExitCode"
    }
    foreach ($path in @($installedApp, $sidecar, $uninstaller)) {
        if (-not (Test-Path $path -PathType Leaf)) {
            throw "Installed file is missing: $path"
        }
    }

    $uninstallEntries = @(Get-RdstUninstallEntries)
    if ($uninstallEntries.Count -ne 1) {
        throw "Expected one Add/Remove Programs entry; found $($uninstallEntries.Count)"
    }
    $uninstallEntry = $uninstallEntries[0]
    if ($uninstallEntry.DisplayVersion -ne $version -or
        $uninstallEntry.Publisher -ne $ExpectedInstalledPublisher) {
        throw "Unexpected Add/Remove Programs metadata: version=$($uninstallEntry.DisplayVersion) publisher=$($uninstallEntry.Publisher)"
    }
    # The installed app updates from the Store channel, the feed the build recorded.
    $installedFeed = Select-String -LiteralPath (Join-Path $installRoot 'resources\app-update.yml') `
        -Pattern '^url:\s*(\S+)' | ForEach-Object { $_.Matches[0].Groups[1].Value }
    if ("$installedFeed" -ne "$($provenance.checks.updateFeed)") {
        throw "Installed Store application updates from '$installedFeed', expected '$($provenance.checks.updateFeed)'"
    }
    $shortcuts = @(Get-RdstStartMenuShortcuts)
    if ($shortcuts.Count -ne 1) {
        throw "Expected one RDST Desktop Start Menu shortcut; found $($shortcuts.Count)"
    }
    if (@(Get-RdstDesktopShortcuts).Count -gt 0) {
        throw 'RDST Desktop Store installer created an unexpected desktop shortcut'
    }
    if (@(Get-RdstServices $installRoot).Count -gt 0 -or
        @(Get-RdstScheduledTasks $installRoot).Count -gt 0) {
        throw 'RDST Desktop installer added an unexpected service or scheduled task'
    }
    $checks.machineInstall = 'passed'

    foreach ($path in @($installedApp, $sidecar, $uninstaller)) {
        Require-FirstPartySignature $path $ExpectedSignaturePublisher
    }
    $portableExecutableExtensions = @('.exe', '.dll', '.node', '.pyd')
    $installedPortableExecutables = @(Get-ChildItem $installRoot -Recurse -File | Where-Object {
        $portableExecutableExtensions -contains $_.Extension.ToLowerInvariant()
    })
    if ($installedPortableExecutables.Count -eq 0) {
        throw 'Installed RDST Desktop tree contains no portable executables'
    }
    $installedSignatureEvidence = @($installedPortableExecutables | ForEach-Object {
        ConvertTo-SignatureEvidence $_ $installRoot
    })
    Write-Json $installedSignatureEvidence (
        Join-Path $certificationDir 'installed-authenticode.json'
    )

    $buildSignaturePath = Join-Path $artifactRoot 'evidence\authenticode.json'
    $buildSignatures = Read-JsonArray $buildSignaturePath
    $buildHashes = @{}
    foreach ($entry in $buildSignatures) {
        $buildHashes["$($entry.path)".ToLowerInvariant()] = "$($entry.sha256)".ToLowerInvariant()
    }
    foreach ($entry in $installedSignatureEvidence) {
        if ($entry.path -eq 'Uninstall RDST Desktop.exe') {
            continue
        }
        $buildPath = "win-unpacked/$($entry.path)".ToLowerInvariant()
        if (-not $buildHashes.ContainsKey($buildPath) -or $buildHashes[$buildPath] -ne $entry.sha256) {
            throw "Installed portable executable differs from the signed build tree: $($entry.path)"
        }
    }
    $checks.authenticode = 'passed'

    Invoke-CapturedChecked $defender @(
        '-Scan', '-ScanType', '3', '-File', $installRoot, '-DisableRemediation'
    ) (Join-Path $certificationDir 'defender-installed.log')
    $checks.defender = 'passed'

    $smokeMarker = Join-Path $stateDir 'SMOKE_OK'
    $env:RDST_DESKTOP_CERTIFICATION = '1'
    $env:RDST_DESKTOP_SMOKE = '1'
    $env:RDST_DESKTOP_SMOKE_MARKER = $smokeMarker
    $env:RDST_TELEMETRY = 'off'
    $env:APPDATA = Join-Path $stateDir 'AppData\Roaming'
    $env:TEMP = Join-Path $stateDir 'Temp'
    $env:TMP = $env:TEMP
    $env:USERPROFILE = $stateDir
    New-Item -ItemType Directory -Force -Path $env:APPDATA, $env:TEMP | Out-Null
    $smokeStdout = Join-Path $certificationDir 'smoke.stdout.log'
    $smokeStderr = Join-Path $certificationDir 'smoke.stderr.log'
    $smokeExitCode = Invoke-BoundedProcess $installedApp `
        @('--disable-gpu', '--disable-dev-shm-usage') 180 $smokeStdout $smokeStderr
    if ($smokeExitCode -ne 0) {
        throw "Installed Store application smoke failed with exit code $smokeExitCode"
    }
    if (-not (Test-Path $smokeMarker -PathType Leaf)) {
        throw 'Installed Store application did not write its smoke marker'
    }
    $smokeOutput = (Get-Content $smokeMarker -Raw).Trim()
    foreach ($required in @(
        'SMOKE_OK',
        'distribution=microsoft-store',
        'updates=enabled',
        'hostedReports=disabled',
        'privacyReset=passed'
    )) {
        if ($smokeOutput -notlike "*${required}*") {
            throw "Installed Store application smoke omitted ${required}: $smokeOutput"
        }
    }
    $checks.launch = 'passed'
    $checks.privacyReset = 'passed'

    Invoke-RdstUninstall $uninstaller $installRoot
    $checks.silentUninstall = 'passed'
} catch {
    $certificationFailure = $_
} finally {
    try {
        $cleanupFailures += @(Disable-NetworkIsolation $networkIsolation)
    } catch {
        $cleanupFailures += "Could not restore network isolation: $($_.Exception.Message)"
    }

    if ($preflightPassed) {
        try {
            $hasInstallationState = (Test-Path $installRoot) -or
                @(Get-RdstUninstallEntries).Count -gt 0
            if ($hasInstallationState -and (Test-Path $uninstaller -PathType Leaf)) {
                Invoke-RdstUninstall $uninstaller $installRoot
            }
        } catch {
            $cleanupFailures += "RDST Desktop uninstaller cleanup failed: $($_.Exception.Message)"
        }

        try {
            $cleanupFailures += @(Remove-RdstPartialState $installRoot)
        } catch {
            $cleanupFailures += "RDST Desktop fallback cleanup failed: $($_.Exception.Message)"
        }

        try {
            if (Test-Path $installRoot) {
                $cleanupFailures += 'RDST Desktop installation directory remained after cleanup'
            }
        } catch {
            $cleanupFailures += "Could not inspect the installation directory: $($_.Exception.Message)"
        }
        try {
            if (@(Get-RdstProtocolKeys).Count -gt 0) {
                $cleanupFailures += 'RDST protocol registration remained after cleanup'
            }
        } catch {
            $cleanupFailures += "Could not inspect protocol registrations: $($_.Exception.Message)"
        }
        try {
            if (@(Get-RdstUninstallEntries).Count -gt 0) {
                $cleanupFailures += 'RDST Desktop Add/Remove Programs entry remained after cleanup'
            }
        } catch {
            $cleanupFailures += "Could not inspect Add/Remove Programs entries: $($_.Exception.Message)"
        }
        try {
            if (@(Get-RdstStartMenuShortcuts).Count -gt 0) {
                $cleanupFailures += 'RDST Desktop Start Menu shortcut remained after cleanup'
            }
        } catch {
            $cleanupFailures += "Could not inspect Start Menu shortcuts: $($_.Exception.Message)"
        }
        try {
            if (@(Get-RdstDesktopShortcuts).Count -gt 0) {
                $cleanupFailures += 'RDST Desktop desktop shortcut remained after cleanup'
            }
        } catch {
            $cleanupFailures += "Could not inspect desktop shortcuts: $($_.Exception.Message)"
        }
        try {
            if (@(Get-RdstProcesses $installRoot).Count -gt 0) {
                $cleanupFailures += 'RDST Desktop process remained after cleanup'
            }
        } catch {
            $cleanupFailures += "Could not inspect RDST Desktop processes: $($_.Exception.Message)"
        }
        try {
            if (@(Get-RdstServices $installRoot).Count -gt 0) {
                $cleanupFailures += 'RDST Desktop service remained after cleanup'
            }
        } catch {
            $cleanupFailures += "Could not inspect Windows services: $($_.Exception.Message)"
        }
        try {
            if (@(Get-RdstScheduledTasks $installRoot).Count -gt 0) {
                $cleanupFailures += 'RDST Desktop scheduled task remained after cleanup'
            }
        } catch {
            $cleanupFailures += "Could not inspect scheduled tasks: $($_.Exception.Message)"
        }
    }

    foreach ($name in $smokeEnvironmentNames) {
        try {
            $originalValue = $originalSmokeEnvironment[$name]
            if ($null -eq $originalValue) {
                if (Test-Path "Env:$name") {
                    Remove-Item "Env:$name" -ErrorAction Stop
                }
            } else {
                Set-Item "Env:$name" $originalValue -ErrorAction Stop
            }
            $restoredValue = [Environment]::GetEnvironmentVariable($name, 'Process')
            if ($restoredValue -ne $originalValue) {
                throw 'Environment variable did not return to its original value'
            }
        } catch {
            $cleanupFailures += "Could not restore environment variable ${name}: $($_.Exception.Message)"
        }
    }
    try {
        Remove-Item $workDir -Recurse -Force -ErrorAction Stop
    } catch {
        $cleanupFailures += "Could not remove certification work directory: $($_.Exception.Message)"
    }
    if ($preflightPassed) {
        try {
            $remaining = @(Get-RdstStateFindings $installRoot)
            $cleanupFailures += $remaining
            if ($remaining.Count -eq 0) {
                Remove-Item -LiteralPath $certificationMarker -Force -ErrorAction Stop
            }
        } catch {
            $cleanupFailures += "Could not verify final RDST state: $($_.Exception.Message)"
        }
    }
    $checks.cleanup = if ($cleanupFailures.Count -eq 0) { 'passed' } else { 'failed' }
}

$requiredChecksPassed = @($checks.Values | Where-Object { $_ -ne 'passed' }).Count -eq 0
$status = if ($null -eq $certificationFailure -and $requiredChecksPassed) {
    'passed'
} else {
    'failed'
}
$networkEvidence = [ordered]@{
    enabledDuringTest = $checks.networkIsolation -eq 'passed'
    applicationScoped = $true
    restored = -not $networkIsolation.enabled
    blockedPrograms = $networkIsolation.programs
    temporaryRules = $networkIsolation.rules
}
Write-Json $networkEvidence (Join-Path $certificationDir 'network-isolation.json')

$certification = [ordered]@{
    schemaVersion = 1
    status = $status
    executionEnvironment = [ordered]@{
        isolation = 'persistent_agent_rdst_state_checked'
        globallyCleanMachine = $false
    }
    installerSha256 = $installerHash
    version = $version
    buildkite = [ordered]@{
        buildNumber = $env:BUILDKITE_BUILD_NUMBER
        commit = $env:BUILDKITE_COMMIT
        buildUrl = $env:BUILDKITE_BUILD_URL
        jobId = $env:BUILDKITE_JOB_ID
    }
    checks = $checks
    error = if ($null -ne $certificationFailure) {
        $certificationFailure.Exception.Message
    } else { $null }
    cleanupErrors = $cleanupFailures
}
Write-Json $certification (Join-Path $certificationDir 'certification.json')
$certificationEvidence = @(Get-ChildItem $certificationDir -File | ForEach-Object {
    [ordered]@{
        path = $_.Name
        size = $_.Length
        sha256 = (Get-FileHash -Algorithm SHA256 -Path $_.FullName).Hash.ToLowerInvariant()
    }
} | Sort-Object { $_.path })
Write-Json $certificationEvidence (
    Join-Path $certificationDir 'certification-evidence.json'
)

if ($env:BUILDKITE) {
    Push-Location $repoRoot
    try {
        Invoke-Checked 'buildkite-agent' @(
            'artifact', 'upload', '--experiment', 'normalised-upload-paths',
            "$certificationRelativeDir/*"
        )
    } finally {
        Pop-Location
    }
}

if ($null -ne $certificationFailure) {
    throw $certificationFailure
}
if ($cleanupFailures.Count -gt 0) {
    throw ($cleanupFailures -join [Environment]::NewLine)
}
