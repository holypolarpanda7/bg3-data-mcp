param([int]$Vk = 0x0D)
Add-Type @"
using System; using System.Runtime.InteropServices;
public class W { [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l); }
"@
$p = Get-Process bg3_dx11,bg3 -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if (-not $p) { "no game window"; exit 1 }
"window: $($p.ProcessName) $($p.MainWindowTitle) $($p.MainWindowHandle)"
[W]::PostMessage($p.MainWindowHandle, 0x0100, [IntPtr]$Vk, [IntPtr]0x001C0001) | Out-Null
Start-Sleep -Milliseconds 80
[W]::PostMessage($p.MainWindowHandle, 0x0101, [IntPtr]$Vk, [IntPtr]0xC01C0001) | Out-Null
"posted key $Vk"
