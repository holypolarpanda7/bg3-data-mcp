# Larian Toolkit (Glasses.exe) automation helper for bg3data/toolkit_ui.py (2026-10-07).
# One command per call; prints one JSON object. The project picker and the menus are WPF and driven through UI Automation
# (stable AutomationIds); the Project Settings window and the Message Log render their own controls (no UIA children), so
# those are clicked at offsets inside the window's UIA rectangle and checked from outside (files, browser windows, screenshots).
param(
    [Parameter(Mandatory = $true)][string]$Cmd,
    [string]$Project = "",
    [string]$Menu = "",
    [string]$Item = "",
    [string]$Title = "",
    [int]$X = 0,
    [int]$Y = 0,
    [string]$Out = "",
    [string]$Location = ""
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes, System.Windows.Forms, System.Drawing
Add-Type @"
using System; using System.Runtime.InteropServices;
public class TkInput {
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint d, UIntPtr e);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
}
"@
$A = [System.Windows.Automation.AutomationElement]; $S = [System.Windows.Automation.TreeScope]; $CT = [System.Windows.Automation.ControlType]
function J($o) { $o | ConvertTo-Json -Compress -Depth 5 }
function Glasses { Get-Process Glasses -ErrorAction SilentlyContinue | Select-Object -First 1 }
function Tops($p) {
    $pc = New-Object System.Windows.Automation.PropertyCondition($A::ProcessIdProperty, $p.Id)
    return $A::RootElement.FindAll($S::Children, $pc)
}
function FindWindow($p, $title) {
    $nc = New-Object System.Windows.Automation.PropertyCondition($A::NameProperty, $title)
    foreach ($t in (Tops $p)) {
        if ($t.Current.Name -eq $title) { return $t }
        $w = $t.FindFirst($S::Children, $nc); if ($w) { return $w }
    }
    return $null
}
function Rect($el) { $r = $el.Current.BoundingRectangle; return @{ x = [int]$r.X; y = [int]$r.Y; w = [int]$r.Width; h = [int]$r.Height } }
function ProjectRows($p) {
    $rb = New-Object System.Windows.Automation.PropertyCondition($A::ControlTypeProperty, $CT::RadioButton)
    $rows = @()
    foreach ($t in (Tops $p)) {
        foreach ($r in $t.FindAll($S::Descendants, $rb)) {
            $name = ""
            foreach ($e in $r.FindAll($S::Descendants, [System.Windows.Automation.Condition]::TrueCondition)) {
                $vp = $null
                if ($e.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$vp) -and $vp.Current.Value) { $name = $vp.Current.Value; break }
            }
            if ($name) { $rows += @{ name = $name; el = $r; top = $t } }
        }
    }
    return $rows
}

$p = Glasses
switch ($Cmd) {
    "state" {
        if (-not $p) { J @{ running = $false }; break }
        $main = (Tops $p) | Where-Object { $_.Current.Name -like "Glasses*" } | Select-Object -First 1
        if (-not $main) { $main = (Tops $p) | Select-Object -First 1 }
        $ps = FindWindow $p "Project Settings"
        $picker = $false
        foreach ($t in (Tops $p)) { if ($t.FindFirst($S::Descendants, (New-Object System.Windows.Automation.PropertyCondition($A::AutomationIdProperty, "m_OpenButton")))) { $picker = $true } }
        J @{ running = $true; pid = $p.Id; title = $main.Current.Name; picker = $picker; settings = $(if ($ps) { Rect $ps } else { $null }) }
    }
    "projects" {
        if (-not $p) { J @{ error = "the Toolkit isn't running" }; break }
        J @{ projects = @((ProjectRows $p) | ForEach-Object { $_.name } | Select-Object -Unique) }
    }
    "open" {
        if (-not $p) { J @{ error = "the Toolkit isn't running" }; break }
        $row = (ProjectRows $p) | Where-Object { $_.name -eq $Project } | Select-Object -First 1
        if (-not $row) { J @{ error = "no project $Project in the picker" }; break }
        $row.el.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Select()
        Start-Sleep -Milliseconds 500
        $btn = $row.top.FindFirst($S::Descendants, (New-Object System.Windows.Automation.PropertyCondition($A::AutomationIdProperty, "m_OpenButton")))
        if (-not $btn -or -not $btn.Current.IsEnabled) { J @{ error = "the Select button isn't enabled after selecting $Project" }; break }
        # Invoke blocks while the project loads (the UI thread is busy) and UIA times out - that's expected, the caller waits
        try { $btn.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke() } catch { }
        J @{ ok = $true; opening = $Project }
    }
    "menu" {
        if (-not $p) { J @{ error = "the Toolkit isn't running" }; break }
        $mi = New-Object System.Windows.Automation.PropertyCondition($A::ControlTypeProperty, $CT::MenuItem)
        $m = $null
        foreach ($t in (Tops $p)) { foreach ($e in $t.FindAll($S::Descendants, $mi)) { if ($e.Current.Name -eq $Menu) { $m = $e; break } }; if ($m) { break } }
        if (-not $m) { J @{ error = "no menu $Menu" }; break }
        $m.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern).Expand()
        Start-Sleep -Milliseconds 700
        $subs = @($m.FindAll($S::Descendants, $mi))
        $names = @($subs | ForEach-Object { $_.Current.Name })
        if ($Item) {
            $t = $subs | Where-Object { $_.Current.Name -eq $Item } | Select-Object -First 1
            if (-not $t) { J @{ error = "no item $Item"; items = $names }; break }
            # a menu item that opens a modal window (Project Settings...) blocks Invoke until UIA times out - expected
            try { $t.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke() } catch { }
            J @{ ok = $true; invoked = $Item }
        } else {
            $m.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern).Collapse()
            J @{ items = $names }
        }
    }
    "rect" {
        if (-not $p) { J @{ error = "the Toolkit isn't running" }; break }
        $w = FindWindow $p $Title
        if (-not $w) { J @{ error = "no window $Title" }; break }
        J (Rect $w)
    }
    "click" {
        if ($p) { [TkInput]::SetForegroundWindow($p.MainWindowHandle) | Out-Null; Start-Sleep -Milliseconds 150 }
        [TkInput]::SetCursorPos($X, $Y) | Out-Null; Start-Sleep -Milliseconds 120
        [TkInput]::mouse_event(0x0002, 0, 0, 0, [UIntPtr]::Zero); Start-Sleep -Milliseconds 60
        [TkInput]::mouse_event(0x0004, 0, 0, 0, [UIntPtr]::Zero)
        J @{ ok = $true; x = $X; y = $Y }
    }
    "shot" {
        $b = [System.Windows.Forms.SystemInformation]::VirtualScreen
        $bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
        $g = [System.Drawing.Graphics]::FromImage($bmp); $g.CopyFromScreen($b.Left, $b.Top, 0, 0, $bmp.Size)
        $img = $bmp
        if ($Title -and $p) {
            $w = FindWindow $p $Title
            if ($w) { $r = Rect $w; $img = $bmp.Clone((New-Object System.Drawing.Rectangle ($r.x - $b.Left), ($r.y - $b.Top), $r.w, $r.h), $bmp.PixelFormat) }
        } else {
            $img = New-Object System.Drawing.Bitmap $bmp, ([int]($b.Width / 2)), ([int]($b.Height / 2))
        }
        $img.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
        J @{ ok = $true; out = $Out }
    }
    "windows" {
        # top-level windows of every process (browser windows the Toolkit opens after a publish, Explorer, dialogs)
        $list = @()
        foreach ($t in $A::RootElement.FindAll($S::Children, [System.Windows.Automation.Condition]::TrueCondition)) {
            $n = $t.Current.Name; if ($n) { $list += @{ name = $n; pid = $t.Current.ProcessId } }
        }
        J @{ windows = $list }
    }
    "closeexplorer" {
        $n = 0
        (New-Object -ComObject Shell.Application).Windows() | Where-Object { $_.LocationName -eq $Location } | ForEach-Object { $_.Quit(); $n++ }
        J @{ closed = $n }
    }
    "quit" {
        if (-not $p) { J @{ ok = $true; running = $false }; break }
        $p.CloseMainWindow() | Out-Null
        J @{ ok = $true; asked = $p.Id }
    }
    default { J @{ error = "unknown command $Cmd" } }
}
