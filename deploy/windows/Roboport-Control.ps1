[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Roboport'),
    # Without -Action: show the control window. With -Action: run that operation headless
    # (the window runs itself this way in a hidden child process so the UI never freezes).
    [ValidateSet('', 'Start', 'Stop', 'StopKeepAutostart', 'Verify')]
    [string]$Action = ''
)
$ErrorActionPreference = 'Stop'
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
$operator = $PSScriptRoot
$taskFile = Join-Path $InstallRoot 'state\startup-task.json'
$taskName = $null
if (Test-Path -LiteralPath $taskFile) { $taskName = (Get-Content -LiteralPath $taskFile -Raw | ConvertFrom-Json).task_name }
$deployment = Get-Content -LiteralPath (Join-Path $InstallRoot 'state\deployment.json') -Raw | ConvertFrom-Json
$opsPort = $deployment.config.ops_port
$launchPad = "http://127.0.0.1:$opsPort/"

function Test-Http([string]$Url) {
    try {
        $req = [Net.WebRequest]::Create($Url); $req.Timeout = 1500
        $resp = $req.GetResponse(); $resp.Close(); return $true
    } catch [Net.WebException] {
        return ($_.Exception.Response -ne $null)   # any HTTP status = something is listening
    } catch { return $false }
}

# ---------------------------------------------------------------- headless actions
if ($Action) {
    switch ($Action) {
        'Stop'              { & (Join-Path $operator 'Stop-Roboport.ps1') -InstallRoot $InstallRoot }
        'StopKeepAutostart' { & (Join-Path $operator 'Stop-Roboport.ps1') -InstallRoot $InstallRoot -KeepAutostart }
        'Verify'            { & (Join-Path $operator 'Verify-Roboport.ps1') -InstallRoot $InstallRoot }
        'Start' {
            if (-not $taskName) { & (Join-Path $operator 'Start-Roboport.ps1') -InstallRoot $InstallRoot; return }
            $task = Get-ScheduledTask -TaskName $taskName
            if ($task.State -eq 'Running') { Write-Host 'Roboport startup task is already running.'; return }
            if ($task.State -eq 'Disabled') {
                try {
                    Enable-ScheduledTask -TaskName $taskName -ErrorAction Stop | Out-Null
                    Write-Host 'Startup task re-enabled.'
                } catch {
                    Write-Warning 'The startup task is disabled and re-enabling it needs admin. Starting Roboport directly in the background instead; it will not autostart at sign-in until the task is re-enabled (run this window as administrator and press Start).'
                    & (Join-Path $operator 'Start-Roboport.ps1') -InstallRoot $InstallRoot
                    return
                }
            }
            Start-ScheduledTask -TaskName $taskName
            Write-Host 'Startup task started; waiting for the Launch Pad...'
            for ($i = 0; $i -lt 30; $i++) {
                Start-Sleep -Seconds 1
                if (Test-Http $launchPad) { Write-Host "Launch Pad is up: $launchPad"; return }
            }
            Write-Warning "Launch Pad did not answer within 30 s. Check $($deployment.data_root)\logs."
        }
    }
    return
}

# ---------------------------------------------------------------- window
# Fully docked layout (resizes cleanly, DPI-aware). Theme: QQ Studios dark - black surfaces,
# white type, gold accents. Assets in .\branding (qq-logo-dark.png; window icon roboport.ico, falling back to qq-dark.ico).
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type -ReferencedAssemblies System.Windows.Forms, System.Drawing -TypeDefinition @'
using System; using System.Drawing; using System.Runtime.InteropServices; using System.Windows.Forms;
public static class QqNative {
    [DllImport("dwmapi.dll")] public static extern int DwmSetWindowAttribute(IntPtr h, int attr, ref int value, int size);
    [DllImport("uxtheme.dll", CharSet = CharSet.Unicode)] public static extern int SetWindowTheme(IntPtr h, string app, string id);
    static int Bgr(Color c) { return c.R | (c.G << 8) | (c.B << 16); }
    // Windows 11: dark title bar, caption/text colours and a coloured window border. Ignored on older builds.
    public static void DarkTitleBar(IntPtr h, Color caption, Color text, Color border) {
        int on = 1; DwmSetWindowAttribute(h, 20, ref on, 4);
        int c = Bgr(caption); DwmSetWindowAttribute(h, 35, ref c, 4);
        int t = Bgr(text); DwmSetWindowAttribute(h, 36, ref t, 4);
        int b = Bgr(border); DwmSetWindowAttribute(h, 34, ref b, 4);
    }
    public static void DarkScrollbars(IntPtr h) { SetWindowTheme(h, "DarkMode_Explorer", null); }
}
// Flat dark renderer for the status strip (the default one paints light gradients).
public class QqStripRenderer : ToolStripProfessionalRenderer {
    readonly Color back, rule;
    public QqStripRenderer(Color back, Color rule) { this.back = back; this.rule = rule; RoundedEdges = false; }
    protected override void OnRenderToolStripBackground(ToolStripRenderEventArgs e) { using (var b = new SolidBrush(back)) e.Graphics.FillRectangle(b, e.AffectedBounds); }
    protected override void OnRenderToolStripBorder(ToolStripRenderEventArgs e) { using (var p = new Pen(rule)) e.Graphics.DrawLine(p, 0, 0, e.ToolStrip.Width, 0); }
    protected override void OnRenderStatusStripSizingGrip(ToolStripRenderEventArgs e) {
        var r = e.AffectedBounds; Color g = Color.FromArgb(212, 175, 55);
        using (var b = new SolidBrush(g)) for (int i = 0; i < 3; i++) for (int j = 0; j <= i; j++)
            e.Graphics.FillRectangle(b, r.Right - 5 - j * 4, r.Bottom - 5 - (2 - i) * 4, 2, 2);
    }
}
'@
[Windows.Forms.Application]::EnableVisualStyles()
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$brandDir = Join-Path $operator 'branding'

function C([int]$r, [int]$g, [int]$b) { [Drawing.Color]::FromArgb($r, $g, $b) }
$void    = C 8 8 8          # header, status strip, title bar
$base    = C 17 17 17       # window body
$card    = C 26 26 26       # status card, output header
$edge    = C 46 46 46       # splitter, rules
$console = C 11 11 11       # output pane
$white   = C 240 240 240
$soft    = C 165 165 165
$gold    = C 212 175 55     # brand accent
$goldHi  = C 236 204 104
$goldLo  = C 168 136 40
$ok      = C 96 214 132
$warn    = C 255 166 77
$bad     = C 255 99 99
$idle    = C 128 128 128

$form = New-Object Windows.Forms.Form
$form.Text = 'Roboport Control  |  QQ Studios  |  ' + $env:COMPUTERNAME + $(if ($isAdmin) { '  (Administrator)' } else { '' })
$form.AutoScaleMode = 'Dpi'
$form.Size = New-Object Drawing.Size(800, 660)
$form.MinimumSize = New-Object Drawing.Size(600, 580)   # keeps a few lines of output visible even when buttons wrap
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'Sizable'
$form.BackColor = $base; $form.ForeColor = $white
$form.Font = New-Object Drawing.Font('Segoe UI', 9.5)
$icoPath = Join-Path $brandDir 'roboport.ico'
if (-not (Test-Path -LiteralPath $icoPath)) { $icoPath = Join-Path $brandDir 'qq-dark.ico' }
if (Test-Path -LiteralPath $icoPath) { $form.Icon = New-Object Drawing.Icon($icoPath) }

# --- header: black bar, logo, white title, gold subtitle, gold rule underneath
$header = New-Object Windows.Forms.TableLayoutPanel
$header.Dock = 'Top'; $header.Height = 92; $header.BackColor = $void; $header.Padding = New-Object Windows.Forms.Padding(14, 12, 14, 12)
$header.ColumnCount = 2; $header.RowCount = 2
[void]$header.ColumnStyles.Add((New-Object Windows.Forms.ColumnStyle('Absolute', 82)))
[void]$header.ColumnStyles.Add((New-Object Windows.Forms.ColumnStyle('Percent', 100)))
[void]$header.RowStyles.Add((New-Object Windows.Forms.RowStyle('Percent', 56)))
[void]$header.RowStyles.Add((New-Object Windows.Forms.RowStyle('Percent', 44)))
$logo = New-Object Windows.Forms.PictureBox
$logo.Dock = 'Fill'; $logo.SizeMode = 'Zoom'; $logo.Margin = New-Object Windows.Forms.Padding(0, 0, 14, 0); $logo.BackColor = $void
$logoPath = Join-Path $brandDir 'qq-logo-dark.png'
if (Test-Path -LiteralPath $logoPath) { $logo.Image = [Drawing.Image]::FromFile($logoPath) }
$header.Controls.Add($logo, 0, 0); $header.SetRowSpan($logo, 2)
$title = New-Object Windows.Forms.Label
$title.Text = 'ROBOPORT CONTROL'; $title.ForeColor = $white; $title.Dock = 'Fill'; $title.TextAlign = 'BottomLeft'
$title.Font = New-Object Drawing.Font('Segoe UI Semibold', 17)
$header.Controls.Add($title, 1, 0)
$subtitle = New-Object Windows.Forms.Label
$subtitle.Text = 'QQ STUDIOS   |   ' + $env:COMPUTERNAME + '   |   ' + $(if ($isAdmin) { 'ADMINISTRATOR' } else { 'STANDARD USER' })
$subtitle.ForeColor = $gold; $subtitle.Dock = 'Fill'; $subtitle.TextAlign = 'TopLeft'
$subtitle.Font = New-Object Drawing.Font('Segoe UI Semibold', 8.5)
$header.Controls.Add($subtitle, 1, 1)
$headerRule = New-Object Windows.Forms.Panel
$headerRule.Dock = 'Top'; $headerRule.Height = 2; $headerRule.BackColor = $gold

# --- status strip (bottom)
$strip = New-Object Windows.Forms.StatusStrip
$strip.SizingGrip = $true; $strip.BackColor = $void; $strip.ForeColor = $soft
$strip.Renderer = New-Object QqStripRenderer($void, $edge)
$stripLeft = New-Object Windows.Forms.ToolStripStatusLabel; $stripLeft.Text = 'Ready'; $stripLeft.Spring = $true; $stripLeft.TextAlign = 'MiddleLeft'; $stripLeft.ForeColor = $soft
$stripRight = New-Object Windows.Forms.ToolStripStatusLabel; $stripRight.Text = 'QQ STUDIOS'; $stripRight.ForeColor = $gold
$stripRight.Font = New-Object Drawing.Font('Segoe UI Semibold', 8)
[void]$strip.Items.Add($stripLeft); [void]$strip.Items.Add($stripRight)

# --- split: controls on top, output below; drag the bar (gold grip) to resize
$split = New-Object Windows.Forms.SplitContainer
$split.Dock = 'Fill'; $split.Orientation = 'Horizontal'; $split.SplitterWidth = 8
$split.BackColor = $edge
$split.Panel1.BackColor = $base; $split.Panel2.BackColor = $console
$split.Panel1MinSize = 180; $split.Panel2MinSize = 90
$split.FixedPanel = 'Panel1'
$split.Add_Paint({
    param($s, $e)
    $r = $s.SplitterRectangle; $cx = [int]($r.X + $r.Width / 2); $cy = [int]($r.Y + $r.Height / 2)
    $pen = New-Object Drawing.Pen($gold, 2)
    $e.Graphics.DrawLine($pen, $cx - 24, $cy, $cx + 24, $cy); $pen.Dispose()
})

$top = New-Object Windows.Forms.TableLayoutPanel
$top.Dock = 'Fill'; $top.ColumnCount = 1; $top.RowCount = 3; $top.Padding = New-Object Windows.Forms.Padding(16, 14, 16, 8); $top.AutoScroll = $true
$top.BackColor = $base
[void]$top.RowStyles.Add((New-Object Windows.Forms.RowStyle('AutoSize')))
[void]$top.RowStyles.Add((New-Object Windows.Forms.RowStyle('AutoSize')))
[void]$top.RowStyles.Add((New-Object Windows.Forms.RowStyle('AutoSize')))
$split.Panel1.Controls.Add($top)

# status card: dark panel with a gold accent stripe on the left
$cardPanel = New-Object Windows.Forms.Panel
$cardPanel.BackColor = $card; $cardPanel.Dock = 'Top'; $cardPanel.AutoSize = $true; $cardPanel.AutoSizeMode = 'GrowAndShrink'
$cardPanel.Padding = New-Object Windows.Forms.Padding(16, 10, 12, 10); $cardPanel.Margin = New-Object Windows.Forms.Padding(0)
$accent = New-Object Windows.Forms.Panel; $accent.Dock = 'Left'; $accent.Width = 3; $accent.BackColor = $gold
$status = New-Object Windows.Forms.TableLayoutPanel
$status.AutoSize = $true; $status.AutoSizeMode = 'GrowAndShrink'; $status.Dock = 'Top'; $status.BackColor = $card
$status.ColumnCount = 2; $status.RowCount = 5
[void]$status.ColumnStyles.Add((New-Object Windows.Forms.ColumnStyle('Absolute', 130)))
[void]$status.ColumnStyles.Add((New-Object Windows.Forms.ColumnStyle('Percent', 100)))
$rows = [ordered]@{}
foreach ($name in 'Roboport', 'Startup task', 'Processes', 'Launch Pad', 'Ollama') {
    $k = New-Object Windows.Forms.Label; $k.Text = $name.ToUpper(); $k.AutoSize = $true; $k.ForeColor = $goldLo
    $k.Font = New-Object Drawing.Font('Segoe UI Semibold', 8.5); $k.Margin = New-Object Windows.Forms.Padding(10, 6, 0, 4)
    $v = New-Object Windows.Forms.Label; $v.Text = '...'; $v.AutoSize = $true; $v.Margin = New-Object Windows.Forms.Padding(0, 4, 0, 4)
    $v.Font = New-Object Drawing.Font('Segoe UI Semibold', 10); $v.ForeColor = $white
    $status.Controls.Add($k); $status.Controls.Add($v); $rows[$name] = $v
}
$cardPanel.Controls.Add($status); $cardPanel.Controls.Add($accent)
$top.Controls.Add($cardPanel, 0, 0)

$buttons = New-Object Windows.Forms.FlowLayoutPanel
$buttons.AutoSize = $true; $buttons.AutoSizeMode = 'GrowAndShrink'; $buttons.WrapContents = $true; $buttons.Dock = 'Top'
$buttons.Margin = New-Object Windows.Forms.Padding(0, 14, 0, 0); $buttons.BackColor = $base
function New-Button([string]$Text, [bool]$Primary) {
    $b = New-Object Windows.Forms.Button
    $b.Text = $Text; $b.AutoSize = $false; $b.Size = New-Object Drawing.Size(112, 36)
    $b.FlatStyle = 'Flat'; $b.Cursor = [Windows.Forms.Cursors]::Hand; $b.Margin = New-Object Windows.Forms.Padding(0, 0, 10, 8)
    $b.Font = New-Object Drawing.Font('Segoe UI Semibold', 9.5); $b.UseVisualStyleBackColor = $false
    $b.FlatAppearance.BorderSize = 1
    if ($Primary) {
        $b.BackColor = $gold; $b.ForeColor = $void; $b.FlatAppearance.BorderColor = $gold
        $b.FlatAppearance.MouseOverBackColor = $goldHi; $b.FlatAppearance.MouseDownBackColor = $goldLo
    } else {
        $b.BackColor = $base; $b.ForeColor = $white; $b.FlatAppearance.BorderColor = $gold
        $b.FlatAppearance.MouseOverBackColor = $card; $b.FlatAppearance.MouseDownBackColor = $edge
    }
    $buttons.Controls.Add($b); return $b
}
$btnStart   = New-Button 'START' $true
$btnStop    = New-Button 'STOP' $true
$btnVerify  = New-Button 'VERIFY' $false
$btnLaunch  = New-Button 'LAUNCH PAD' $false
$btnConsole = New-Button 'CONSOLE' $false
$btnRefresh = New-Button 'REFRESH' $false
$top.Controls.Add($buttons, 0, 1)

$chkKeep = New-Object Windows.Forms.CheckBox
$chkKeep.Text = 'Stop keeps autostart on (maintenance stop)'; $chkKeep.AutoSize = $true
$chkKeep.ForeColor = $soft; $chkKeep.BackColor = $base; $chkKeep.FlatStyle = 'Flat'
$chkKeep.FlatAppearance.BorderColor = $gold; $chkKeep.FlatAppearance.CheckedBackColor = $gold
$chkKeep.Margin = New-Object Windows.Forms.Padding(2, 0, 0, 0)
$top.Controls.Add($chkKeep, 0, 2)

$outHeader = New-Object Windows.Forms.Label
$outHeader.Text = '   OUTPUT'; $outHeader.Dock = 'Top'; $outHeader.Height = 26; $outHeader.TextAlign = 'MiddleLeft'
$outHeader.BackColor = $card; $outHeader.ForeColor = $gold; $outHeader.Font = New-Object Drawing.Font('Segoe UI Semibold', 8.5)
$log = New-Object Windows.Forms.TextBox
$log.Multiline = $true; $log.ReadOnly = $true; $log.ScrollBars = 'Vertical'; $log.WordWrap = $true
$log.Font = New-Object Drawing.Font('Consolas', 9.5); $log.BackColor = $console; $log.ForeColor = (C 214 214 214)
$log.BorderStyle = 'None'; $log.Dock = 'Fill'
$logWrap = New-Object Windows.Forms.Panel
$logWrap.Dock = 'Fill'; $logWrap.BackColor = $console; $logWrap.Padding = New-Object Windows.Forms.Padding(14, 8, 4, 4)
$logWrap.Controls.Add($log)
$split.Panel2.Controls.Add($outHeader)
$split.Panel2.Controls.Add($logWrap)
$logWrap.BringToFront()      # dock order: header first, output fills the rest

$form.Controls.Add($split)
$form.Controls.Add($headerRule)
$form.Controls.Add($header)
$form.Controls.Add($strip)
$split.BringToFront()        # fill what is left between header + rule (top) and strip (bottom)

function Add-Log([string]$Text) {
    if (-not $Text) { return }
    $log.AppendText(($Text -replace "`r?`n", "`r`n"))
    if (-not $Text.EndsWith("`n")) { $log.AppendText("`r`n") }
}
function Set-Row([string]$Name, [string]$Text, [Drawing.Color]$Color) { $rows[$Name].Text = $Text; $rows[$Name].ForeColor = $Color }

function Update-Status {
    $release = $deployment.release_root
    $procs = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -and $_.CommandLine.IndexOf($release, [StringComparison]::OrdinalIgnoreCase) -ge 0 })
    $taskState = 'not registered'
    if ($taskName) { try { $taskState = [string](Get-ScheduledTask -TaskName $taskName -ErrorAction Stop).State } catch { $taskState = 'not found' } }
    $up = Test-Http $launchPad
    if ($up) { Set-Row 'Roboport' 'Running' $ok }
    elseif ($procs.Count -gt 0) { Set-Row 'Roboport' 'Starting or partly running' $warn }
    else { Set-Row 'Roboport' 'Stopped' $bad }
    switch ($taskState) {
        'Running'  { Set-Row 'Startup task' 'Running (autostart enabled)' $ok }
        'Ready'    { Set-Row 'Startup task' 'Ready (autostart enabled, not running)' $warn }
        'Disabled' { Set-Row 'Startup task' 'Disabled (no autostart at sign-in)' $bad }
        default    { Set-Row 'Startup task' $taskState $idle }
    }
    Set-Row 'Processes' ("{0} Roboport python process(es)" -f $procs.Count) $(if ($procs.Count) { $ok } else { $idle })
    Set-Row 'Launch Pad' ($(if ($up) { "Up    $launchPad" } else { "Down    $launchPad" })) $(if ($up) { $ok } else { $bad })
    $ollamaUrl = ([string]$deployment.config.ollama_url).TrimEnd('/')
    if (-not $ollamaUrl) { $ollamaUrl = 'http://127.0.0.1:11434' }
    $ollamaUp = Test-Http ($ollamaUrl + '/api/version')
    Set-Row 'Ollama' $(if ($ollamaUp) { "Up    $ollamaUrl" } else { "Down    $ollamaUrl (Roboport needs it for runs)" }) $(if ($ollamaUp) { $ok } else { $bad })
    $btnLaunch.Enabled = $up -and -not $script:job
    $btnConsole.Enabled = $up -and -not $script:job
    if (-not $script:job) { $stripLeft.Text = 'Status refreshed ' + (Get-Date -Format 'HH:mm:ss') }
}

# One action at a time, in a hidden child powershell.exe; a timer streams its output files.
$script:job = $null
$timer = New-Object Windows.Forms.Timer; $timer.Interval = 500
$refreshTicks = 0
function Read-New([string]$Path, [ref]$Offset) {
    if (-not (Test-Path -LiteralPath $Path)) { return '' }
    $fs = [IO.File]::Open($Path, 'Open', 'Read', 'ReadWrite')
    try {
        if ($fs.Length -le $Offset.Value) { return '' }
        [void]$fs.Seek($Offset.Value, 'Begin')
        $sr = New-Object IO.StreamReader($fs); $text = $sr.ReadToEnd(); $Offset.Value = $fs.Length; return $text
    } finally { $fs.Dispose() }
}
function Set-Busy([bool]$Busy) {
    foreach ($b in $btnStart, $btnStop, $btnVerify, $btnRefresh, $chkKeep) { $b.Enabled = -not $Busy }
    $form.UseWaitCursor = $Busy
}
function Start-Action([string]$Name, [string]$Label) {
    if ($script:job) { return }
    $out = [IO.Path]::GetTempFileName(); $err = [IO.Path]::GetTempFileName()
    $argLine = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -InstallRoot "{1}" -Action {2}' -f $PSCommandPath, $InstallRoot, $Name
    Add-Log ("`r`n=== {0}  {1} ===" -f (Get-Date -Format 'HH:mm:ss'), $Label)
    # Workers run in Windows PowerShell 5.1 even when this window runs in pwsh.
    $p = Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList $argLine -WindowStyle Hidden -PassThru -RedirectStandardOutput $out -RedirectStandardError $err
    $null = $p.Handle   # cache the handle now; without it .NET reports a blank ExitCode once the process exits
    $script:job =@{ Process = $p; Out = $out; Err = $err; OutPos = [long]0; ErrPos = [long]0; Label = $Label }
    $stripLeft.Text = "Running: $Label ..."; $stripLeft.ForeColor = $gold
    Set-Busy $true
}
$timer.Add_Tick({
    if ($script:job) {
        $j = $script:job
        $o = $j.OutPos; $e = $j.ErrPos
        Add-Log (Read-New $j.Out ([ref]$o)); Add-Log (Read-New $j.Err ([ref]$e))
        $j.OutPos = $o; $j.ErrPos = $e
        if ($j.Process.HasExited) {
            $o = $j.OutPos; $e = $j.ErrPos
            Add-Log (Read-New $j.Out ([ref]$o)); Add-Log (Read-New $j.Err ([ref]$e))
            $j.Process.WaitForExit()
            $code = $j.Process.ExitCode
            $msg = $(if ($null -eq $code) { "$($j.Label): finished (exit code unavailable - check the output above)" }
                     elseif ($code -eq 0) { "$($j.Label): done" }
                     else { "$($j.Label): FAILED (exit $code)" })
            Add-Log "--- $msg"
            Remove-Item $j.Out, $j.Err -ErrorAction SilentlyContinue
            $script:job = $null; Set-Busy $false; Update-Status
            $stripLeft.Text = $msg + '  (' + (Get-Date -Format 'HH:mm:ss') + ')'
            $stripLeft.ForeColor = $(if ($code -eq 0) { $soft } elseif ($null -eq $code) { $warn } else { $bad })
        }
    } else {
        $script:refreshTicks++
        if ($script:refreshTicks -ge 10) { $script:refreshTicks = 0; Update-Status }   # every 5 s when idle
    }
})

$btnStart.Add_Click({ Start-Action 'Start' 'Start' })
$btnStop.Add_Click({
    if ($chkKeep.Checked) { Start-Action 'StopKeepAutostart' 'Stop (keep autostart)' }
    else {
        if (-not $isAdmin) { Add-Log 'Note: not running as administrator, so the startup task cannot be disabled; Stop will warn and still stop Roboport.' }
        Start-Action 'Stop' 'Stop'
    }
})
$btnVerify.Add_Click({ Start-Action 'Verify' 'Verify (takes a minute or two)' })
$btnLaunch.Add_Click({ Start-Process $launchPad })
# Opens the Launch Pad on Fleet -> Topology, where the Ops Console is embedded and fed by the gateway.
$btnConsole.Add_Click({ Start-Process ($launchPad + '#fleet') })
$btnRefresh.Add_Click({ Update-Status; Add-Log ("Status refreshed {0}" -f (Get-Date -Format 'HH:mm:ss')) })
$form.Add_HandleCreated({
    try { [QqNative]::DarkTitleBar($form.Handle, $void, $white, $gold) } catch {}
})
$form.Add_Shown({
    try { [QqNative]::DarkScrollbars($log.Handle); [QqNative]::DarkScrollbars($top.Handle) } catch {}
    # Splitter starts just below the controls; drag it to change.
    $want = $top.PreferredSize.Height + 8
    if ($want -lt $split.Panel1MinSize) { $want = $split.Panel1MinSize }
    if ($want -lt ($split.Height - $split.Panel2MinSize - $split.SplitterWidth)) { $split.SplitterDistance = $want }
    Update-Status; $timer.Start()
})
$form.Add_FormClosing({
    param($sender, $e)
    if ($script:job -and -not $script:job.Process.HasExited) {
        $r = [Windows.Forms.MessageBox]::Show('An action is still running in the background. Close the window anyway? (The action keeps running.)', 'Roboport Control', 'YesNo', 'Warning')
        if ($r -ne 'Yes') { $e.Cancel = $true; return }
    }
    $timer.Stop()
})
Add-Log ("Roboport Control. Install: {0}" -f $InstallRoot)
[void]$form.ShowDialog()
