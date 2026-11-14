Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Read-DeploymentJson([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Missing deployment file: $Path" }
    return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
}

function Assert-Windows {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { throw 'This command requires Windows. No changes made.' }
}

function Invoke-Checked([string]$Executable, [string[]]$Arguments) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Executable" }
}

function Get-Deployment([string]$InstallRoot) {
    $manifest = Read-DeploymentJson (Join-Path $InstallRoot 'state\deployment.json')
    $root = [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\') + '\'
    $release = [IO.Path]::GetFullPath($manifest.release_root)
    if (-not $release.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) { throw 'Release path escapes the installation.' }
    if (-not (Test-Path -LiteralPath (Join-Path $release '.venv\Scripts\python.exe'))) { throw 'Deployment Python is missing. Run setup again.' }
    return $manifest
}

function Write-DeploymentJson([string]$Path, $Value) {
    $temporary = "$Path.tmp"
    $Value | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

function Quote-WindowsArgument([string]$Value) {
    # Windows argv quoting; spaces, embedded quotes, and trailing backslashes.
    if ($Value -notmatch '[\s"]' -and $Value.Length -gt 0) { return $Value }
    $quoted = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $quoted = [regex]::Replace($quoted, '(\\+)$', '$1$1')
    return '"' + $quoted + '"'
}

function Get-OptionalProperty($Object, [string]$Name) {
    if ($null -ne $Object -and $Object.PSObject.Properties[$Name]) { return $Object.$Name }
    return $null
}

function Set-MachineBinding($Config, [string]$InstallRoot) {
    # An installation is bound to the machine it was set up on. A configuration
    # may still pin expected_machine; an existing installation keeps its machine.
    $machine = $env:COMPUTERNAME
    $pinned = Get-OptionalProperty $Config 'expected_machine'
    if ($pinned -and $pinned -ne $machine) { throw "This configuration is pinned to $pinned, but this machine is $machine. Review configuration before proceeding." }
    $active = Join-Path $InstallRoot 'state\deployment.json'
    if (Test-Path -LiteralPath $active -PathType Leaf) {
        $bound = Get-OptionalProperty (Get-OptionalProperty (Read-DeploymentJson $active) 'config') 'expected_machine'
        if ($bound -and $bound -ne $machine) { throw "This installation belongs to $bound, but this machine is $machine. Choose a new install root on this machine." }
    }
    $Config | Add-Member -NotePropertyName expected_machine -NotePropertyValue $machine -Force
    return $machine
}

function Select-HardwareTier($Config, [string]$GpuInfo, [double]$MemoryBytes) {
    # Tiers are measured capacity, not product names. Each tier lists the models
    # approved for it; adding a tier or a model goes through governance.
    $tiers = (Read-DeploymentJson (Join-Path $PSScriptRoot 'hardware-tiers.json')).tiers
    $wanted = Get-OptionalProperty $Config 'hardware_tier'
    $names = if ($wanted) { @([string]$wanted) } else { @($tiers.PSObject.Properties.Name) }
    $fits = @()
    foreach ($name in $names) {
        $tier = Get-OptionalProperty $tiers $name
        if (-not $tier) { throw "Unknown hardware tier: $name" }
        $vram = 0
        foreach ($line in ($GpuInfo -split "`r?`n")) {
            if ($line -match [regex]::Escape([string]$tier.gpu_vendor) -and $line -match ',\s*(\d+)\s*MiB') { $vram = [math]::Max($vram, [int]$Matches[1]) }
        }
        # Windows reports installed RAM minus firmware-reserved memory, so a
        # 64 GiB machine shows slightly less; allow 2% for that.
        if ($vram -ge $tier.min_vram_mib -and $MemoryBytes -ge ([double]$tier.min_ram_gib * 1GB * 0.98)) {
            $fits += $name
            if (@($tier.models) -contains [string]$Config.model) {
                $Config | Add-Member -NotePropertyName hardware_tier -NotePropertyValue $name -Force
                return [ordered]@{ name = $name; vram_mib = $vram; min_free_disk_gib = [double]$tier.min_free_disk_gib }
            }
        }
    }
    if ($fits.Count) { throw "Model $($Config.model) is not approved for hardware tier $($fits -join ', '). Model changes go through Roboport governance." }
    throw "This machine does not meet hardware tier $($names -join ', '). See deploy\windows\hardware-tiers.json."
}

function Get-DeploymentPreflight($Config, [string]$InstallRoot) {
    Assert-Windows
    $machine = Set-MachineBinding $Config $InstallRoot
    $node = Get-Command node -ErrorAction SilentlyContinue
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    $py = Get-Command py -ErrorAction SilentlyContinue
    $ollama = Get-Command ollama -ErrorAction SilentlyContinue
    $gpu = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if (-not $node -or -not $npm -or -not $py -or -not $ollama -or -not $gpu) {
        throw 'Prerequisites missing. Install Python 3.12 (with py launcher), Node 24 LTS, Ollama, and the NVIDIA driver. Reopen PowerShell. Setup has not changed security settings.'
    }
    $pythonVersion = (& $py.Source -3.12 --version 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $pythonVersion -notmatch '^Python 3\.12\.') { throw 'Python 3.12 is required.' }
    $nodeVersion = (& $node.Source --version | Out-String).Trim().TrimStart('v')
    $nv = [version]$nodeVersion
    if (-not (($nv.Major -eq 22 -and $nv -ge [version]'22.19.0') -or ($nv.Major -eq 24 -and $nv -ge [version]'24.11.0') -or $nv.Major -ge 26)) { throw "Unsupported Node version: $nodeVersion" }
    $gpuInfo = (& $gpu.Source --query-gpu=name,memory.total,driver_version --format=csv,noheader | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $gpuInfo) { throw 'NVIDIA driver is not ready.' }
    $memory = (Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory
    $tier = Select-HardwareTier $Config $gpuInfo $memory
    $volume = [IO.Path]::GetPathRoot([IO.Path]::GetFullPath($InstallRoot))
    $disk = New-Object IO.DriveInfo($volume)
    if ($disk.AvailableFreeSpace -lt ($tier.min_free_disk_gib * 1GB)) { throw "At least $($tier.min_free_disk_gib) GiB free disk space is required for the initial model and release." }
    foreach ($port in @($Config.ops_port, $Config.feed_port)) {
        $listener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback, [int]$port)
        try { $listener.Start() } finally { $listener.Stop() }
    }
    return [ordered]@{ machine = $machine; hardware_tier = $tier.name; gpu_vram_mib = $tier.vram_mib; python = $pythonVersion; node = $nodeVersion; gpu = $gpuInfo; memory_bytes = $memory; disk_free_bytes = $disk.AvailableFreeSpace; py = $py.Source; npm = $npm.Source; ollama = $ollama.Source }
}
