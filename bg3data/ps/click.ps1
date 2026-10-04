param([int]$X = 0, [int]$Y = 0, [double]$Fx = -1, [double]$Fy = -1, [switch]$Right, [int]$Count = 1)
# OS-level mouse click at (X, Y) in the GAME's client area pixels (the game is brought to the front first).
Add-Type @"
using System; using System.Text; using System.Runtime.InteropServices;
public class CK {
  public delegate bool P(IntPtr h, IntPtr l);
  [StructLayout(LayoutKind.Sequential)] public struct PT { public int X, Y; }
  [StructLayout(LayoutKind.Sequential)] public struct MI { public int dx, dy; public uint data, flags, time; public IntPtr extra; }
  [StructLayout(LayoutKind.Sequential)] public struct INPUT { public uint type; public MI mi; }
  [DllImport("user32.dll")] public static extern bool EnumWindows(P f, IntPtr l);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [StructLayout(LayoutKind.Sequential)] public struct RC { public int Left, Top, Right, Bottom; }
  [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out RC r);
  [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr h, ref PT p);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern uint SendInput(uint n, INPUT[] i, int size);
  public static IntPtr Find(uint want) { IntPtr found = IntPtr.Zero; EnumWindows((h, l) => { uint p; GetWindowThreadProcessId(h, out p);
    if (p == want && IsWindowVisible(h)) { var c = new StringBuilder(64); GetClassName(h, c, 64); if (c.ToString() == "SDL_app") { found = h; return false; } } return true; }, IntPtr.Zero); return found; }
  public static void Btn(uint flags) { var i = new INPUT[1]; i[0].type = 0; i[0].mi.flags = flags; SendInput(1, i, Marshal.SizeOf(typeof(INPUT))); } }
"@
$h = [IntPtr]::Zero
foreach ($p in (Get-Process bg3_dx11,bg3 -ErrorAction SilentlyContinue)) { $h = [CK]::Find([uint32]$p.Id); if ($h -ne [IntPtr]::Zero) { break } }
if ($h -eq [IntPtr]::Zero) { "no game window"; exit 1 }
& (Join-Path $PSScriptRoot "focus_game.ps1") | Out-Null; Start-Sleep -Milliseconds 300
if ($Fx -ge 0 -and $Fy -ge 0) {  # fractions of the client area: independent of the window resolution
  $rc = New-Object CK+RC; [CK]::GetClientRect($h, [ref]$rc) | Out-Null
  $X = [int](($rc.Right - $rc.Left) * $Fx); $Y = [int](($rc.Bottom - $rc.Top) * $Fy) }
$pt = New-Object CK+PT; $pt.X = $X; $pt.Y = $Y; [CK]::ClientToScreen($h, [ref]$pt) | Out-Null
[CK]::SetCursorPos($pt.X, $pt.Y) | Out-Null; Start-Sleep -Milliseconds 250
$down = if ($Right) { 0x0008 } else { 0x0002 }; $up = if ($Right) { 0x0010 } else { 0x0004 }
for ($n = 0; $n -lt $Count; $n++) { [CK]::Btn($down); Start-Sleep -Milliseconds 80; [CK]::Btn($up); Start-Sleep -Milliseconds 120 }
"clicked $($pt.X),$($pt.Y)"
