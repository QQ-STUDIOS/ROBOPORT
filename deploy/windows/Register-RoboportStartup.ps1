[CmdletBinding()]
param([string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Roboport'))
. (Join-Path $PSScriptRoot 'Common.ps1')
Assert-Windows
$manifest = Get-Deployment $InstallRoot
$script = Join-Path $InstallRoot 'operator\Start-Roboport.ps1'
$arguments = '-NoProfile -File ' + (Quote-WindowsArgument $script) + ' -InstallRoot ' + (Quote-WindowsArgument ([IO.Path]::GetFullPath($InstallRoot))) + ' -Foreground'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$suffix = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes([IO.Path]::GetFullPath($InstallRoot))).Replace('/', '_').Replace('+', '-').TrimEnd('=')
$name = 'Roboport-' + $suffix.Substring(0, [Math]::Min(100, $suffix.Length))
$action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument $arguments
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Write-DeploymentJson (Join-Path $InstallRoot 'state\startup-task.json') ([ordered]@{ task_name = $name; user = $identity; action_executable = $action.Execute; action_arguments = $arguments; recovery = 'after account sign-in following reboot' })
Write-Host "Registered $name for $identity at sign-in. No stored password or security-policy change. Sign in after reboot to verify recovery."
