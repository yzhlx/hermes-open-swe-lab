[CmdletBinding()]
param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$ScriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepositoryRoot = Resolve-Path (Join-Path $ScriptDirectory "..\..")
$ArtifactRoot = Join-Path (
    [System.IO.Path]::GetTempPath()
) ("hermes-workbench-acceptance-" + [Guid]::NewGuid().ToString("N"))

New-Item -ItemType Directory -Path $ArtifactRoot | Out-Null

function Invoke-PytestGroup {
    param(
        [string]$Name,
        [string[]]$Paths
    )

    $JunitPath = Join-Path $ArtifactRoot ($Name + ".xml")
    $PytestArguments = @(
        "-m", "pytest",
        "-q",
        "-p", "no:cacheprovider"
    ) + $Paths + @("--junitxml=$JunitPath")

    $Timer = [System.Diagnostics.Stopwatch]::StartNew()
    & $Python @PytestArguments 2>&1 | Out-Host
    $ExitCode = $LASTEXITCODE
    $Timer.Stop()

    $Tests = 0
    $Failures = 0
    $Errors = 0
    $Skipped = 0
    if (Test-Path -LiteralPath $JunitPath) {
        [xml]$Document = Get-Content -Raw -LiteralPath $JunitPath
        $Suites = @()
        if ($null -ne $Document.testsuites) {
            $Suites = @($Document.testsuites.testsuite)
        } elseif ($null -ne $Document.testsuite) {
            $Suites = @($Document.testsuite)
        }
        foreach ($Suite in $Suites) {
            $Tests += [int]$Suite.tests
            $Failures += [int]$Suite.failures
            $Errors += [int]$Suite.errors
            $Skipped += [int]$Suite.skipped
        }
    } else {
        $Errors = 1
    }

    return [pscustomobject]@{
        name = $Name
        exit_code = $ExitCode
        tests = $Tests
        failures = $Failures
        errors = $Errors
        skipped = $Skipped
        duration_ms = $Timer.ElapsedMilliseconds
        junit = $JunitPath
    }
}

function Invoke-SecretScan {
    param([string]$ArtifactPath)

    $Scanner = Join-Path $ScriptDirectory "secret_scan.py"
    $ScannerArguments = @(
        $Scanner,
        "--repo", "$RepositoryRoot",
        "--output", "$ArtifactPath"
    )
    $ScannerOutput = & $Python @ScannerArguments 2>&1
    $ScanExitCode = $LASTEXITCODE
    $ScannerOutput | Out-Host

    if (-not (Test-Path -LiteralPath $ArtifactPath -PathType Leaf)) {
        return [pscustomobject]@{
            exit_code = 1
            files_scanned = 0
            candidate_count = 0
            violation_count = 0
            violation_paths = @()
            error_count = 1
            error_paths = @("<scanner-artifact-missing>")
            artifact = $ArtifactPath
        }
    }

    try {
        $Result = Get-Content -Raw -LiteralPath $ArtifactPath |
            ConvertFrom-Json
    } catch {
        return [pscustomobject]@{
            exit_code = 1
            files_scanned = 0
            candidate_count = 0
            violation_count = 0
            violation_paths = @()
            error_count = 1
            error_paths = @("<scanner-artifact-invalid>")
            artifact = $ArtifactPath
        }
    }
    if ([int]$Result.exit_code -ne $ScanExitCode) {
        $Result.exit_code = 1
    }
    $Result | Add-Member -NotePropertyName artifact -NotePropertyValue $ArtifactPath -Force
    return $Result
}

Push-Location $RepositoryRoot
try {
    $env:PYTHONDONTWRITEBYTECODE = "1"

    # These groups are deliberately offline: no provider-live test, gh, Docker,
    # push, PR creation, or external network operation is invoked.
    $Results = @()
    $Results += Invoke-PytestGroup -Name "workbench" -Paths @(
        "tests/workbench"
    )
    $Results += Invoke-PytestGroup -Name "baseline-43" -Paths @(
        "tests/test_d3_closed_loop.py",
        "tests/test_d3_security.py",
        "tests/test_codex_host_flow.py"
    )
    $Results += Invoke-PytestGroup -Name "host-rework-security" -Paths @(
        "tests/test_codex_host_rework.py"
    )
    $Results += Invoke-PytestGroup -Name "redact" -Paths @(
        "tests/test_redact.py"
    )

    git diff --check
    $DiffExitCode = $LASTEXITCODE
    git diff --cached --check
    $CachedDiffExitCode = $LASTEXITCODE
    $SecretScanPath = Join-Path $ArtifactRoot "secret-scan.json"
    $SecretScan = Invoke-SecretScan -ArtifactPath $SecretScanPath

    $Failed = (
        $DiffExitCode -ne 0 -or
        $CachedDiffExitCode -ne 0 -or
        $SecretScan.exit_code -ne 0
    )
    foreach ($Result in $Results) {
        if (
            $Result.exit_code -ne 0 -or
            $Result.failures -ne 0 -or
            $Result.errors -ne 0 -or
            $Result.skipped -ne 0
        ) {
            $Failed = $true
        }
    }
    $BaselineResult = $Results |
        Where-Object { $_.name -eq "baseline-43" } |
        Select-Object -First 1
    if ($null -eq $BaselineResult -or $BaselineResult.tests -ne 43) {
        $Failed = $true
    }

    $Summary = [pscustomobject]@{
        status = $(if ($Failed) { "FAIL" } else { "PASS" })
        offline = $true
        groups = $Results
        git_diff_check_exit = $DiffExitCode
        git_cached_diff_check_exit = $CachedDiffExitCode
        secret_scan = $SecretScan
        artifacts = $ArtifactRoot
    }
    $Summary | ConvertTo-Json -Depth 5

    if ($Failed) {
        exit 1
    }
    exit 0
} finally {
    Pop-Location
}
