param([string]$Out)
# Capture the game window (class SDL_app) to a PNG.
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System; using System.Text; using System.Runtime.InteropServices;
public class SS {
  public delegate bool P(IntPtr h, IntPtr l);
  [StructLayout(LayoutKind.Sequential)] public struct RC { public int Left, Top, Right, Bottom; }
  [StructLayout(LayoutKind.Sequential)] public struct PT { public int X, Y; }
  [DllImport("user32.dll")] public static extern bool EnumWindows(P f, IntPtr l);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out RC r);
  [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr h, ref PT p);
  public static IntPtr Find(uint want) { IntPtr found = IntPtr.Zero; EnumWindows((h, l) => { uint p; GetWindowThreadProcessId(h, out p);
    if (p == want && IsWindowVisible(h)) { var c = new StringBuilder(64); GetClassName(h, c, 64); if (c.ToString() == "SDL_app") { found = h; return false; } } return true; }, IntPtr.Zero); return found; } }
"@
$h = [IntPtr]::Zero
foreach ($p in (Get-Process bg3_dx11,bg3 -ErrorAction SilentlyContinue)) { $h = [SS]::Find([uint32]$p.Id); if ($h -ne [IntPtr]::Zero) { break } }
if ($h -eq [IntPtr]::Zero) { "no game window"; exit 1 }
$r = New-Object SS+RC; [SS]::GetClientRect($h, [ref]$r) | Out-Null
$pt = New-Object SS+PT; [SS]::ClientToScreen($h, [ref]$pt) | Out-Null
$w = $r.Right - $r.Left; $hgt = $r.Bottom - $r.Top
$bmp = New-Object System.Drawing.Bitmap $w, $hgt
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($pt.X, $pt.Y, 0, 0, (New-Object System.Drawing.Size $w, $hgt))
# scale down to keep the file small
$sw = [int]($w / 2); $sh = [int]($hgt / 2)
$small = New-Object System.Drawing.Bitmap $bmp, $sw, $sh
$small.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
"saved $Out ${w}x${hgt} (scaled ${sw}x${sh})"
