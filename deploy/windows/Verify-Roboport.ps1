[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Roboport'),
    [switch]$RestartCheck,
    [switch]$RecoveryCheck,
    [switch]$RollbackCheck
)
. (Join-Path $PSScriptRoot 'Common.ps1')
Assert-Windows
$manifest = Get-Deployment $InstallRoot
$python = Join-Path $manifest.release_root '.venv\Scripts\python.exe'
$report = Join-Path $manifest.release_root 'scripts\deployment_report.py'
# Exit code 2 means "checks pending", so a missing script must not reach Python.
if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $report)) { throw "Release is incomplete: $report or its Python environment is missing." }
$arguments = @($report, '--install', [IO.Path]::GetFullPath($InstallRoot))
if ($RestartCheck) { $arguments += '--restart-check' }
if ($RecoveryCheck) { $arguments += '--recovery-check' }
if ($RollbackCheck) { $arguments += '--rollback-check' }
& $python @arguments
$result = $LASTEXITCODE
Write-Host "Report: $($manifest.data_root)\deployment-report.json"
if ($result -eq 2) { Write-Warning 'Live verification may have passed, but required recovery/rollback observations are still pending. Read the report.'; return }
if ($result -ne 0) { throw 'Deployment verification failed. Inspect the individual checks and benchmark logs.' }
Write-Host 'All required deployment, governed launch pad walkthrough, and recovery checks passed on this machine.'
