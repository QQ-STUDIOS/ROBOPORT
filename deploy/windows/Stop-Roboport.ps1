[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Roboport'),
    # Stop the running deployment but leave the sign-in startup task enabled (maintenance stop).
    [switch]$KeepAutostart
)
. (Join-Path $PSScriptRoot 'Common.ps1')
Assert-Windows
$manifest = Get-Deployment $InstallRoot
$taskFile = Join-Path $InstallRoot 'state\startup-task.json'
$task = $null
if (Test-Path -LiteralPath $taskFile) { $task = Read-DeploymentJson $taskFile }

if ($task) {
    # 1. Autostart. Disabling needs the same rights the task was registered with (elevated);
    #    a non-elevated stop warns and carries on instead of aborting before anything is stopped.
    if ($KeepAutostart) {
        Write-Host 'Startup task left enabled (-KeepAutostart). Roboport starts again at next sign-in.'
    } else {
        try {
            Disable-ScheduledTask -TaskName $task.task_name -ErrorAction Stop | Out-Null
            Write-Host 'Startup task disabled.'
        } catch {
            Write-Warning "Could not disable the startup task ($($_.Exception.Message.Trim())). Autostart stays enabled; run elevated to disable it. Continuing with the stop."
        }
    }
    # 2. End the running task instance (its PowerShell wrapper) so the task shows Ready, not Running.
    try {
        $current = Get-ScheduledTask -TaskName $task.task_name -ErrorAction Stop
        if ($current.State -eq 'Running') {
            Stop-ScheduledTask -TaskName $task.task_name -ErrorAction Stop
            Write-Host 'Startup task instance ended.'
        }
    } catch {
        Write-Warning "Could not end the startup task instance ($($_.Exception.Message.Trim())). Continuing."
    }
}

# 3. Stop the owned processes: supervisor first, then its children (see windows_deploy.py stop_services).
Invoke-Checked (Join-Path $manifest.release_root '.venv\Scripts\python.exe') @((Join-Path $manifest.release_root 'scripts\windows_deploy.py'), 'stop', '--install', [IO.Path]::GetFullPath($InstallRoot))
Write-Host 'Stopped owned deployment processes. Ollama and unrelated processes were preserved.'
