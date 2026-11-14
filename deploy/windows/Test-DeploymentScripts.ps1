[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$failures = @()
Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.ps1' | ForEach-Object {
    $tokens = $null
    $errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile($_.FullName, [ref]$tokens, [ref]$errors) | Out-Null
    $failures += $errors
}
if ($failures.Count) { $failures | Format-List; throw 'PowerShell parsing failed.' }
. (Join-Path $PSScriptRoot 'Common.ps1')
if ((Quote-WindowsArgument 'C:\space path\') -ne '"C:\space path\\"') { throw 'Trailing slash argv quoting failed.' }
if ((Quote-WindowsArgument 'a"b') -ne '"a\"b"') { throw 'Embedded quote argv quoting failed.' }
$missing = Join-Path ([IO.Path]::GetTempPath()) ([Guid]::NewGuid().ToString() + '.json')
try { Read-DeploymentJson $missing; throw 'Missing config was accepted.' } catch { if ($_.Exception.Message -eq 'Missing config was accepted.') { throw } }

function Assert-Throws([scriptblock]$Action, [string]$Pattern, [string]$Label) {
    $message = $null
    try { & $Action } catch { $message = $_.Exception.Message }
    if (-not $message -or $message -notmatch $Pattern) { throw "$Label was not rejected as expected: $message" }
}
function New-TestConfig { return (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'deployment.example.json') -Raw | ConvertFrom-Json) }

# Hardware tiers: measured capacity and the tier's approved models, never a product name.
$gpuA = 'NVIDIA GeForce RTX 5090, 32607 MiB, 617.42'
$config = New-TestConfig
if ((Select-HardwareTier $config $gpuA (127GB)).name -ne 'A' -or $config.hardware_tier -ne 'A') { throw 'Tier A workstation was not matched.' }
$config = New-TestConfig
if ((Select-HardwareTier $config 'NVIDIA RTX 6000 Ada Generation, 49140 MiB, 560.1' (64GB)).name -ne 'A') { throw 'Equal-capacity GPU was not matched.' }
Assert-Throws { Select-HardwareTier (New-TestConfig) 'NVIDIA GeForce RTX 4090, 24564 MiB, 560.1' (128GB) } 'does not meet hardware tier' 'Low-VRAM GPU'
Assert-Throws { Select-HardwareTier (New-TestConfig) $gpuA (32GB) } 'does not meet hardware tier' 'Low-RAM machine'
$config = New-TestConfig; $config.model = 'unreviewed:70b'
Assert-Throws { Select-HardwareTier $config $gpuA (128GB) } 'not approved for hardware tier' 'Unapproved model'
$config = New-TestConfig; $config.hardware_tier = 'Z'
Assert-Throws { Select-HardwareTier $config $gpuA (128GB) } 'Unknown hardware tier' 'Unknown tier'

# Machine binding: recorded at install; a pin or an existing install for another machine is refused.
$root = Join-Path ([IO.Path]::GetTempPath()) ('roboport-binding-' + [Guid]::NewGuid().ToString())
try {
    $config = New-TestConfig
    if ((Set-MachineBinding $config $root) -ne $env:COMPUTERNAME -or $config.expected_machine -ne $env:COMPUTERNAME) { throw 'Machine was not bound at install.' }
    $config = New-TestConfig
    $config | Add-Member -NotePropertyName expected_machine -NotePropertyValue 'OTHER-MACHINE-X'
    Assert-Throws { Set-MachineBinding $config $root } 'pinned to OTHER-MACHINE-X' 'Pin to another machine'
    New-Item -ItemType Directory -Path (Join-Path $root 'state') -Force | Out-Null
    @{ config = @{ expected_machine = 'OTHER-MACHINE-X' } } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $root 'state\deployment.json') -Encoding UTF8
    Assert-Throws { Set-MachineBinding (New-TestConfig) $root } 'belongs to OTHER-MACHINE-X' 'Install for another machine'
} finally { Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue }
Write-Host 'PowerShell syntax, argument quoting, missing-config, hardware tier and machine binding checks passed. Hardware/startup checks still require the target.'
