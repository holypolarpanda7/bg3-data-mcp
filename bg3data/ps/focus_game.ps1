# Bring the game window (class SDL_app) to the foreground. Player-side interrupt decisions may need the client
# to be active (2026-10-02 investigation). The Alt tap lets SetForegroundWindow through Windows' focus rules.
Add-Type @"
using System; using System.Text; using System.Runtime.InteropServices;
public class F {
  public delegate bool P(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(P f, IntPtr l);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
  public static IntPtr Find(uint want) { IntPtr found = IntPtr.Zero; EnumWindows((h, l) => { uint p; GetWindowThreadProcessId(h, out p);
    if (p == want && IsWindowVisible(h)) { var c = new StringBuilder(64); GetClassName(h, c, 64);
      if (c.ToString() == "SDL_app") { found = h; return false; } } return true; }, IntPtr.Zero); return found; } }
"@
$h = [IntPtr]::Zero
foreach ($p in (Get-Process bg3_dx11,bg3 -ErrorAction SilentlyContinue)) { $h = [F]::Find([uint32]$p.Id); if ($h -ne [IntPtr]::Zero) { break } }
if ($h -eq [IntPtr]::Zero) { "no game window"; exit 1 }
[F]::ShowWindow($h, 9) | Out-Null
[F]::keybd_event(0x12, 0, 0, [UIntPtr]::Zero); [F]::keybd_event(0x12, 0, 2, [UIntPtr]::Zero)
[F]::SetForegroundWindow($h) | Out-Null
Start-Sleep -Milliseconds 300
if ([F]::GetForegroundWindow() -eq $h) { "foreground" } else { "not foreground" }
