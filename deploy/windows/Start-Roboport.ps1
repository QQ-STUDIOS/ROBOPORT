[CmdletBinding()]
param([string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Roboport'), [switch]$Foreground)
. (Join-Path $PSScriptRoot 'Common.ps1')
Assert-Windows
$manifest = Get-Deployment $InstallRoot
$python = Join-Path $manifest.release_root '.venv\Scripts\python.exe'
$helper = Join-Path $manifest.release_root 'scripts\windows_deploy.py'
$arguments = @($helper, 'serve', '--install', [IO.Path]::GetFullPath($InstallRoot))
if ($Foreground) { Invoke-Checked $python $arguments; return }
$logRoot = Join-Path $manifest.data_root 'logs'
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
$commandLine = ($arguments | ForEach-Object { Quote-WindowsArgument $_ }) -join ' '
$process = Start-Process -FilePath $python -ArgumentList $commandLine -WorkingDirectory $manifest.release_root -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logRoot 'supervisor.out.log') -RedirectStandardError (Join-Path $logRoot 'supervisor.err.log')
Start-Sleep -Seconds 2
if ($process.HasExited) { throw "Startup failed. Inspect $logRoot\supervisor.err.log" }
Write-Host "Started supervisor PID $($process.Id). Launch Pad (Live): http://127.0.0.1:$($manifest.config.ops_port)/; feed: http://127.0.0.1:$($manifest.config.feed_port)/roboport-feed.html?live=1"
