param([int]$Vk = 0x0D)
# Post a key to the GAME window (class SDL_app). Not Process.MainWindowHandle: with the Script Extender console
# open that is sometimes the console window (seen 2026-10-01: 8 Enters went to the console, splash never cleared).
Add-Type @"
using System; using System.Text; using System.Runtime.InteropServices;
public class W {
  public delegate bool P(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(P f, IntPtr l);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
  public static IntPtr Find(uint want) { IntPtr found = IntPtr.Zero; EnumWindows((h, l) => { uint p; GetWindowThreadProcessId(h, out p);
    if (p == want && IsWindowVisible(h)) { var c = new StringBuilder(64); GetClassName(h, c, 64);
      if (c.ToString() == "SDL_app") { found = h; return false; } } return true; }, IntPtr.Zero); return found; } }
"@
$h = [IntPtr]::Zero
foreach ($p in (Get-Process bg3_dx11,bg3 -ErrorAction SilentlyContinue)) { $h = [W]::Find([uint32]$p.Id); if ($h -ne [IntPtr]::Zero) { break } }
if ($h -eq [IntPtr]::Zero) { "no game window (SDL_app)"; exit 1 }
"window: $h"
[W]::PostMessage($h, 0x0100, [IntPtr]$Vk, [IntPtr]0x001C0001) | Out-Null
Start-Sleep -Milliseconds 80
[W]::PostMessage($h, 0x0101, [IntPtr]$Vk, [IntPtr]0xC01C0001) | Out-Null
"posted key $Vk"
