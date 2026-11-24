Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Require-PowerShellSyntax {
    param([Parameter(Mandatory = $true)][string]$Path)

    $tokens = $null
    $errors = $null
    [Management.Automation.Language.Parser]::ParseFile(
        $Path,
        [ref]$tokens,
        [ref]$errors
    ) | Out-Null
    if ($errors.Count -gt 0) {
        $messages = @($errors | ForEach-Object { $_.Message }) -join '; '
        throw "PowerShell syntax errors in ${Path}: $messages"
    }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path
$certificationScript = Join-Path $PSScriptRoot 'certify-win-store.ps1'
foreach ($path in @(
    $MyInvocation.MyCommand.Path,
    $certificationScript,
    (Join-Path $repoRoot 'rdst\.buildkite\build_rdst_desktop_windows.ps1')
)) {
    Require-PowerShellSyntax $path
}

. $certificationScript -FunctionsOnly

$tasks = @(
    [pscustomobject]@{
        TaskName = 'Unrelated COM task'
        Actions = @([pscustomobject]@{ ClassId = '{00000000-0000-0000-0000-000000000000}' })
    },
    [pscustomobject]@{
        TaskName = 'RDST task'
        Actions = @([pscustomobject]@{ Execute = 'C:\Program Files\RDST Desktop\rdst.exe' })
    }
)
$matchingTasks = @(Get-RdstScheduledTasks 'C:\Program Files\RDST Desktop' $tasks)
if ($matchingTasks.Count -ne 1 -or $matchingTasks[0].TaskName -ne 'RDST task') {
    throw 'Scheduled-task inspection did not safely select the RDST executable action'
}

$credentialListing = @(
    'Currently stored credentials:',
    '    Target: LegacyGeneric:target=rdst-web',
    '    Type: Generic',
    '    Target: LegacyGeneric:target=alice@rdst-slack',
    '    Target: LegacyGeneric:target=rdst-website-deploy',
    '    Target: WindowsLive:target=virtualapp/didlogical'
)
$credentialTargets = @(Get-RdstCredentialTargets $credentialListing)
if (($credentialTargets -join ',') -ne 'rdst-web,alice@rdst-slack') {
    throw "Credential inspection selected the wrong entries: $($credentialTargets -join ', ')"
}

$jsonArrayPath = Join-Path ([IO.Path]::GetTempPath()) "rdst-store-json-$PID.json"
try {
    [IO.File]::WriteAllText($jsonArrayPath, '[{"path": "a.exe"}, {"path": "b.dll"}]')
    $entries = Read-JsonArray $jsonArrayPath
    if ($entries.Count -ne 2 -or $entries[1].path -ne 'b.dll') {
        throw "JSON arrays read as $($entries.Count) entries"
    }
} finally {
    Remove-Item -LiteralPath $jsonArrayPath -ErrorAction SilentlyContinue
}

$updaterCache = Join-Path ([IO.Path]::GetTempPath()) "rdst-store-updater-$PID"
try {
    New-Item -ItemType Directory -Path $updaterCache | Out-Null
    $installerCopy = Join-Path $updaterCache 'installer.exe'
    [IO.File]::WriteAllText($installerCopy, 'installer')
    $openInstaller = [IO.File]::Open($installerCopy, 'Open', 'Read', 'None')
    try {
        $problem = Remove-RdstUpdaterCache $updaterCache 1
    } finally {
        $openInstaller.Dispose()
    }
    if ($problem -notlike '*installer.exe (9 bytes)*') {
        throw "A locked updater cache was reported as: $problem"
    }
    $problem = Remove-RdstUpdaterCache $updaterCache 5
    if ($problem -or (Test-Path -LiteralPath $updaterCache)) {
        throw "An unlocked updater cache remained: $problem"
    }
} finally {
    Remove-Item -LiteralPath $updaterCache -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host 'RDST Desktop Store PowerShell checks passed'
