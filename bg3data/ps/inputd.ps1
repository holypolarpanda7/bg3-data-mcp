# Long-lived input helper for the MCP (Windows). Compiles the Win32 glue ONCE, then answers one-line commands on stdin
# with one-line replies on stdout, so a click costs milliseconds instead of a new PowerShell + Add-Type (~2 s) each time.
#   focus                       bring the game window (class SDL_app) to the front (no-op when it already is)
#   key SCAN [HOLD_MS]          SendInput hardware scan code (game focused first)
#   click FX FY [L|R] [COUNT]   mouse click at fractions (0..1) of the client area
#   clickpx X Y [L|R] [COUNT]   mouse click at client pixels
#   shot PATH                   PNG of the game window, half size (game focused first); replies "ok WxH SWxSH"
#   lum FX FY FW FH             mean brightness (0-255) of a region (fractions of the client area), no focus change
#   rgb FX FY FW FH             mean R G B of a region (fractions of the client area)
#   redrows FX FY FW FH         client height, then the y (client px) of red "!" markers inside that strip, comma separated
#   tiles FX FY FW FH           icon-like blobs (bright, 24-70 px squares) inside a region: "ok H x:y,x:y,..." (client px)
#   ping
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System; using System.Text; using System.Runtime.InteropServices;
public class ID {
  public delegate bool P(IntPtr h, IntPtr l);
  [StructLayout(LayoutKind.Sequential)] public struct PT { public int X, Y; }
  [StructLayout(LayoutKind.Sequential)] public struct RC { public int Left, Top, Right, Bottom; }
  [StructLayout(LayoutKind.Sequential)] public struct KI { public ushort wVk; public ushort wScan; public uint dwFlags; public uint time; public IntPtr extra; }
  [StructLayout(LayoutKind.Sequential)] public struct MI { public int dx, dy; public uint data, dwFlags, time; public IntPtr extra; }
  [StructLayout(LayoutKind.Explicit)] public struct IU { [FieldOffset(0)] public KI ki; [FieldOffset(0)] public MI mi; }
  [StructLayout(LayoutKind.Sequential)] public struct INPUT { public uint type; public IU u; }
  [DllImport("user32.dll")] public static extern bool EnumWindows(P f, IntPtr l);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
  [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out RC r);
  [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr h, ref PT p);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll", SetLastError=true)] public static extern uint SendInput(uint n, INPUT[] i, int size);
  public static IntPtr Find(uint want) { IntPtr found = IntPtr.Zero; EnumWindows((h, l) => { uint p; GetWindowThreadProcessId(h, out p);
    if (p == want && IsWindowVisible(h)) { var c = new StringBuilder(64); GetClassName(h, c, 64);
      if (c.ToString() == "SDL_app") { found = h; return false; } } return true; }, IntPtr.Zero); return found; }
  public static void Key(ushort scan, bool up) { var i = new INPUT[1]; i[0].type = 1; i[0].u.ki.wScan = scan;
    i[0].u.ki.dwFlags = 0x0008 | (up ? 0x0002u : 0u); SendInput(1, i, Marshal.SizeOf(typeof(INPUT))); }
  public static void Btn(uint flags) { var i = new INPUT[1]; i[0].type = 0; i[0].u.mi.dwFlags = flags; SendInput(1, i, Marshal.SizeOf(typeof(INPUT))); } }
"@

$script:hwnd = [IntPtr]::Zero
function Get-Game {
    if ($script:hwnd -ne [IntPtr]::Zero -and [ID]::IsWindow($script:hwnd)) { return $script:hwnd }
    $script:hwnd = [IntPtr]::Zero
    foreach ($p in (Get-Process bg3_dx11, bg3 -ErrorAction SilentlyContinue)) {
        $h = [ID]::Find([uint32]$p.Id); if ($h -ne [IntPtr]::Zero) { $script:hwnd = $h; break } }
    return $script:hwnd
}
function Set-Focus {
    $h = Get-Game
    if ($h -eq [IntPtr]::Zero) { throw "no game window" }
    if ([ID]::GetForegroundWindow() -eq $h) { return }   # already in front: nothing to wait for
    [ID]::ShowWindow($h, 9) | Out-Null
    [ID]::keybd_event(0x12, 0, 0, [UIntPtr]::Zero); [ID]::keybd_event(0x12, 0, 2, [UIntPtr]::Zero)  # Alt tap: foreground rules
    [ID]::SetForegroundWindow($h) | Out-Null
    for ($i = 0; $i -lt 20 -and [ID]::GetForegroundWindow() -ne $h; $i++) { Start-Sleep -Milliseconds 25 }
    if ([ID]::GetForegroundWindow() -ne $h) { throw "game not foreground" }
}
function Do-Click([int]$x, [int]$y, [string]$btn, [int]$n) {
    Set-Focus
    $h = Get-Game
    $pt = New-Object ID+PT; $pt.X = $x; $pt.Y = $y; [ID]::ClientToScreen($h, [ref]$pt) | Out-Null
    [ID]::SetCursorPos($pt.X + 4, $pt.Y + 3) | Out-Null; Start-Sleep -Milliseconds 25   # the UI needs a real move to hover a target
    [ID]::SetCursorPos($pt.X, $pt.Y) | Out-Null; Start-Sleep -Milliseconds 45
    $down = if ($btn -eq "R") { 0x0008 } else { 0x0002 }; $up = if ($btn -eq "R") { 0x0010 } else { 0x0004 }
    for ($i = 0; $i -lt $n; $i++) { [ID]::Btn($down); Start-Sleep -Milliseconds 35; [ID]::Btn($up); Start-Sleep -Milliseconds 60 }
}
function Client-Size {
    $rc = New-Object ID+RC; [ID]::GetClientRect((Get-Game), [ref]$rc) | Out-Null
    return @(($rc.Right - $rc.Left), ($rc.Bottom - $rc.Top))
}

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
while ($true) {
    $line = [Console]::In.ReadLine()
    if ($null -eq $line) { break }
    $a = $line.Trim() -split '\s+'
    try {
        switch ($a[0]) {
            "ping"  { $r = "ok" }
            "focus" { Set-Focus; $r = "ok" }
            "key"   { Set-Focus; $hold = if ($a.Count -gt 2) { [int]$a[2] } else { 60 }
                      [ID]::Key([uint16][Convert]::ToInt32($a[1], 10), $false); Start-Sleep -Milliseconds $hold
                      [ID]::Key([uint16][Convert]::ToInt32($a[1], 10), $true); $r = "ok" }
            "click" { $s = Client-Size
                      $b = if ($a.Count -gt 3) { $a[3] } else { "L" }; $n = if ($a.Count -gt 4) { [int]$a[4] } else { 1 }
                      Do-Click ([int]($s[0] * [double]$a[1])) ([int]($s[1] * [double]$a[2])) $b $n; $r = "ok" }
            "clickpx" { $b = if ($a.Count -gt 3) { $a[3] } else { "L" }; $n = if ($a.Count -gt 4) { [int]$a[4] } else { 1 }
                      Do-Click ([int]$a[1]) ([int]$a[2]) $b $n; $r = "ok" }
            "shot"  { Set-Focus; Start-Sleep -Milliseconds 80
                      $h = Get-Game; $s = Client-Size
                      $pt = New-Object ID+PT; [ID]::ClientToScreen($h, [ref]$pt) | Out-Null
                      $bmp = New-Object System.Drawing.Bitmap $s[0], $s[1]
                      $g = [System.Drawing.Graphics]::FromImage($bmp)
                      $g.CopyFromScreen($pt.X, $pt.Y, 0, 0, (New-Object System.Drawing.Size $s[0], $s[1]))
                      $sw = [int]($s[0] / 2); $sh = [int]($s[1] / 2)
                      $small = New-Object System.Drawing.Bitmap $bmp, $sw, $sh
                      $small.Save($line.Substring(5).Trim(), [System.Drawing.Imaging.ImageFormat]::Png)
                      $g.Dispose(); $bmp.Dispose(); $small.Dispose()
                      $r = "ok $($s[0])x$($s[1]) ${sw}x${sh}" }
            "lum"   { $h = Get-Game; $s = Client-Size
                      $pt = New-Object ID+PT; [ID]::ClientToScreen($h, [ref]$pt) | Out-Null
                      $w = [int]($s[0] * [double]$a[3]); $hh = [int]($s[1] * [double]$a[4])
                      $bmp = New-Object System.Drawing.Bitmap $w, $hh
                      $g = [System.Drawing.Graphics]::FromImage($bmp)
                      $g.CopyFromScreen($pt.X + [int]($s[0] * [double]$a[1]), $pt.Y + [int]($s[1] * [double]$a[2]), 0, 0, (New-Object System.Drawing.Size $w, $hh))
                      $sm = New-Object System.Drawing.Bitmap $bmp, 8, 8; $t = 0
                      for ($y = 0; $y -lt 8; $y++) { for ($x = 0; $x -lt 8; $x++) { $c = $sm.GetPixel($x, $y); $t += 0.299 * $c.R + 0.587 * $c.G + 0.114 * $c.B } }
                      $g.Dispose(); $bmp.Dispose(); $sm.Dispose(); $r = "ok " + [int]($t / 64) }
            "rgb"   { $h = Get-Game; $s = Client-Size
                      $pt = New-Object ID+PT; [ID]::ClientToScreen($h, [ref]$pt) | Out-Null
                      $w = [Math]::Max(1, [int]($s[0] * [double]$a[3])); $hh = [Math]::Max(1, [int]($s[1] * [double]$a[4]))
                      $bmp = New-Object System.Drawing.Bitmap $w, $hh
                      $g = [System.Drawing.Graphics]::FromImage($bmp)
                      $g.CopyFromScreen($pt.X + [int]($s[0] * [double]$a[1]), $pt.Y + [int]($s[1] * [double]$a[2]), 0, 0, (New-Object System.Drawing.Size $w, $hh))
                      $sm = New-Object System.Drawing.Bitmap $bmp, 4, 4; $cr = 0; $cg = 0; $cb = 0
                      for ($y = 0; $y -lt 4; $y++) { for ($x = 0; $x -lt 4; $x++) { $c = $sm.GetPixel($x, $y); $cr += $c.R; $cg += $c.G; $cb += $c.B } }
                      $g.Dispose(); $bmp.Dispose(); $sm.Dispose(); $r = "ok " + [int]($cr / 16) + " " + [int]($cg / 16) + " " + [int]($cb / 16) }
            "redrows" { # redrows FX FY FW FH: a strip given in fractions of the client area; replies "ok H y,y,..." (client px)
                      $h = Get-Game; $s = Client-Size
                      $pt = New-Object ID+PT; [ID]::ClientToScreen($h, [ref]$pt) | Out-Null
                      $x0 = [int]($s[0] * [double]$a[1]); $y0 = [int]($s[1] * [double]$a[2])
                      $w = [Math]::Max(4, [int]($s[0] * [double]$a[3])); $hh = [Math]::Max(4, [int]($s[1] * [double]$a[4]))
                      $bmp = New-Object System.Drawing.Bitmap $w, $hh
                      $g = [System.Drawing.Graphics]::FromImage($bmp)
                      $g.CopyFromScreen($pt.X + $x0, $pt.Y + $y0, 0, 0, (New-Object System.Drawing.Size $w, $hh))
                      $minh = [Math]::Max(2, [int]($s[1] / 270))
                      $rows = @(); $in = $false; $start = 0
                      for ($y = 0; $y -le $hh; $y++) { $hit = $false
                        if ($y -lt $hh) { for ($x = 0; $x -lt $w; $x++) { $c = $bmp.GetPixel($x, $y); if ($c.R -gt 150 -and $c.G -lt 90 -and $c.B -lt 100) { $hit = $true; break } } }
                        if ($hit) { if (-not $in) { $in = $true; $start = $y } } elseif ($in) { $in = $false; if ($y - $start -ge $minh) { $rows += [int]($y0 + ($start + $y) / 2) } } }
                      $g.Dispose(); $bmp.Dispose(); $r = "ok " + $s[1] + " " + ($rows -join ",") }
            "tiles" { $h = Get-Game; $s = Client-Size
                      $pt = New-Object ID+PT; [ID]::ClientToScreen($h, [ref]$pt) | Out-Null
                      $x0 = [int]($s[0] * [double]$a[1]); $y0 = [int]($s[1] * [double]$a[2])
                      $w = [Math]::Max(8, [int]($s[0] * [double]$a[3])); $hh = [Math]::Max(8, [int]($s[1] * [double]$a[4]))
                      $bmp = New-Object System.Drawing.Bitmap $w, $hh
                      $g = [System.Drawing.Graphics]::FromImage($bmp)
                      $g.CopyFromScreen($pt.X + $x0, $pt.Y + $y0, 0, 0, (New-Object System.Drawing.Size $w, $hh))
                      $cs = [Math]::Max(3, [int]($s[1] / 270)); $cw = [int]($w / $cs); $ch = [int]($hh / $cs)
                      $sm = New-Object System.Drawing.Bitmap $bmp, $cw, $ch          # one pixel per cell = the cell's mean colour
                      $on = New-Object 'bool[,]' $cw, $ch
                      for ($y = 0; $y -lt $ch; $y++) { for ($x = 0; $x -lt $cw; $x++) { $c = $sm.GetPixel($x, $y)
                        $on[$x, $y] = (0.299 * $c.R + 0.587 * $c.G + 0.114 * $c.B) -ge 70 } }
                      $seen = New-Object 'bool[,]' $cw, $ch; $out = @()
                      $minc = [int](0.022 * $s[1] / $cs); $maxc = [int](0.065 * $s[1] / $cs) + 1
                      for ($y = 0; $y -lt $ch; $y++) { for ($x = 0; $x -lt $cw; $x++) {
                        if (-not $on[$x, $y] -or $seen[$x, $y]) { continue }
                        $stack = New-Object System.Collections.Stack; $stack.Push(@($x, $y)); $seen[$x, $y] = $true
                        $n = 0; $lx = $x; $hx = $x; $ly = $y; $hy = $y
                        while ($stack.Count -gt 0) { $p = $stack.Pop(); $px = $p[0]; $py = $p[1]; $n++
                          if ($px -lt $lx) { $lx = $px }; if ($px -gt $hx) { $hx = $px }; if ($py -lt $ly) { $ly = $py }; if ($py -gt $hy) { $hy = $py }
                          foreach ($d in @(@(1,0),@(-1,0),@(0,1),@(0,-1))) { $nx = $px + $d[0]; $ny = $py + $d[1]
                            if ($nx -ge 0 -and $ny -ge 0 -and $nx -lt $cw -and $ny -lt $ch -and $on[$nx, $ny] -and -not $seen[$nx, $ny]) { $seen[$nx, $ny] = $true; $stack.Push(@($nx, $ny)) } } }
                        $bw = $hx - $lx + 1; $bh = $hy - $ly + 1
                        if ($bw -ge $minc -and $bh -ge $minc -and $bw -le $maxc -and $bh -le $maxc -and $n -ge 0.4 * $bw * $bh) {
                          $out += ("" + [int]($x0 + ($lx + $hx + 1) * $cs / 2) + ":" + [int]($y0 + ($ly + $hy + 1) * $cs / 2)) } } }
                      $g.Dispose(); $bmp.Dispose(); $sm.Dispose(); $r = "ok " + $s[1] + " " + ($out -join ",") }
            default { $r = "err unknown command" }
        }
    } catch { $r = "err " + $_.Exception.Message }
    [Console]::Out.WriteLine($r); [Console]::Out.Flush()
}
