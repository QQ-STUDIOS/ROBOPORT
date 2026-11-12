[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Roboport'),
    [string]$ConfigPath = (Join-Path $PSScriptRoot 'deployment.example.json'),
    [switch]$InstallMissing,
    [switch]$PullModel,
    [switch]$PreflightOnly
)
. (Join-Path $PSScriptRoot 'Common.ps1')
Assert-Windows
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
$config = Read-DeploymentJson $ConfigPath
if ($PreflightOnly -and ($InstallMissing -or $PullModel)) { throw 'PreflightOnly cannot install prerequisites or download a model.' }
if ($InstallMissing) {
    $winget = Get-Command winget -ErrorAction Stop
    foreach ($item in @(@('py', 'Python.Python.3.12'), @('node', 'OpenJS.NodeJS.LTS'), @('ollama', 'Ollama.Ollama'))) {
        if (-not (Get-Command $item[0] -ErrorAction SilentlyContinue)) {
            Invoke-Checked $winget.Source @('install', '--id', $item[1], '--exact', '--source', 'winget', '--accept-package-agreements', '--accept-source-agreements')
        }
    }
    $env:PATH = [Environment]::GetEnvironmentVariable('PATH', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('PATH', 'User')
}
$preflight = Get-DeploymentPreflight $config $InstallRoot
$preflight | ConvertTo-Json -Depth 10
if ($PreflightOnly) { return }
if (Test-Path -LiteralPath (Join-Path $InstallRoot 'state\processes.json')) { throw 'Stop the active deployment before setup/update; recover a stale ownership record explicitly.' }
$source = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$sourcePrefix = $source.TrimEnd('\') + '\'
if (($InstallRoot + '\').StartsWith($sourcePrefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Choose an install root outside the source package.' }
$state = Join-Path $InstallRoot 'state'
$data = Join-Path $InstallRoot 'data'
New-Item -ItemType Directory -Path $state, $data -Force | Out-Null
# Credentials and classified evidence inherit only the deploying account,
# SYSTEM, and administrators. Startup runs under this same limited account.
$owner = [Security.Principal.WindowsIdentity]::GetCurrent().Name
Invoke-Checked (Join-Path $env:SystemRoot 'System32\icacls.exe') @($data, '/inheritance:r', '/grant:r', ($owner + ':(OI)(CI)F'), '*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F')
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfffZ')
$release = Join-Path $InstallRoot "releases\$stamp"
New-Item -ItemType Directory -Path $release | Out-Null
# Explicit allowlist excludes repository internals, local overrides, and secrets.
foreach ($name in @('agents', 'config', 'resources', 'workflows', 'evals', 'scripts', 'dashboard', 'control_surface', 'tests', 'integrations', 'deploy', 'docs', 'stations', 'README.md', 'LICENSE', 'requirements.txt')) {
    $from = Join-Path $source $name
    if (Test-Path -LiteralPath $from) {
        if (Test-Path -LiteralPath $from -PathType Leaf) { Copy-Item -LiteralPath $from -Destination $release -Force; continue }
        & robocopy $from (Join-Path $release $name) /E /XD .git .venv node_modules __pycache__ /XF '*.local.*' '.env' '.env.*' '*.log' /NFL /NDL /NJH /NJS /NP | Out-Null
        if (Test-Path -LiteralPath $from -PathType Leaf) { Copy-Item -LiteralPath $from -Destination $release -Force }
        elseif ($LASTEXITCODE -ge 8) { throw "Release copy failed: $name" }
    }
}
Invoke-Checked $preflight.py @('-3.12', '-m', 'venv', (Join-Path $release '.venv'))
$python = Join-Path $release '.venv\Scripts\python.exe'
Invoke-Checked $python @('-m', 'pip', 'install', '--upgrade', 'pip==26.2.1')
Invoke-Checked $python @('-m', 'pip', 'install', '-r', (Join-Path $release 'deploy\windows\requirements.lock.txt'))
Push-Location (Join-Path $release 'integrations\rea')
try { Invoke-Checked $preflight.npm @('ci', '--ignore-scripts', '--no-audit', '--no-fund') } finally { Pop-Location }
Invoke-Checked $python @((Join-Path $release 'scripts\windows_deploy.py'), 'configure', '--release', $release, '--data', $data, '--config', ([IO.Path]::GetFullPath($ConfigPath)), '--machine', $preflight.machine, '--hardware-tier', $preflight.hardware_tier)
if ($PullModel) { Invoke-Checked $preflight.ollama @('pull', [string]$config.model) }
Push-Location $release
try { Invoke-Checked $python @('scripts\validate.py', '--all') } finally { Pop-Location }
$active = Join-Path $state 'deployment.json'
if (Test-Path -LiteralPath $active) { Copy-Item -LiteralPath $active -Destination (Join-Path $state 'previous-deployment.json') -Force }
$manifest = [ordered]@{ schema_version = 1; release_root = $release; data_root = $data; config = $config; protected_runs = @(); created_at = $stamp; status = 'installed_unverified'; preflight = $preflight }
Write-DeploymentJson $active $manifest
New-Item -ItemType Directory -Path (Join-Path $InstallRoot 'operator') -Force | Out-Null
Get-ChildItem -LiteralPath (Join-Path $release 'deploy\windows') | Copy-Item -Destination (Join-Path $InstallRoot 'operator') -Recurse -Force
Write-Host "Launch pad identities and policy are unconfigured. Enroll named users with scripts\launchpad_admin.py in the active release. A station pack's setup notes are under stations\<name>\docs."
Write-Host "Bound to $($preflight.machine), hardware tier $($preflight.hardware_tier)."
Write-Host "Installed, not yet verified. Start Ollama, then run: & '$InstallRoot\operator\Start-Roboport.ps1' -InstallRoot '$InstallRoot'"
