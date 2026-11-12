[CmdletBinding()]
param([string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Roboport'))
. (Join-Path $PSScriptRoot 'Common.ps1')
Assert-Windows
$previousPath = Join-Path $InstallRoot 'state\previous-deployment.json'
$previous = Read-DeploymentJson $previousPath
if (-not (Test-Path -LiteralPath (Join-Path $previous.release_root '.venv\Scripts\python.exe'))) { throw 'Previous release is unavailable; nothing changed.' }
& (Join-Path $PSScriptRoot 'Stop-Roboport.ps1') -InstallRoot $InstallRoot
$activePath = Join-Path $InstallRoot 'state\deployment.json'
Copy-Item -LiteralPath $activePath -Destination (Join-Path $InstallRoot 'state\rolled-back-deployment.json') -Force
Write-DeploymentJson $activePath $previous
# Restore operator scripts as well as the Python and npm dependency environment.
Remove-Item -LiteralPath (Join-Path $InstallRoot 'operator') -Recurse -Force
Copy-Item -LiteralPath (Join-Path $previous.release_root 'deploy\windows') -Destination (Join-Path $InstallRoot 'operator') -Recurse
& (Join-Path $InstallRoot 'operator\Start-Roboport.ps1') -InstallRoot $InstallRoot
Write-Host 'Restored previous release and configuration; run evidence was preserved. Re-run verification.'
