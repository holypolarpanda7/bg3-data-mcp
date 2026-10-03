param([int]$Scan = 0x2E, [int]$HoldMs = 120, [switch]$NoFocus)
# OS-level key press (SendInput, hardware scan code) - what gameplay hotkeys read (PostMessage and the Script Extender's
# Ext.Input only reach the UI layer). The game must be the foreground window, so focus it first (unless -NoFocus);
# this types into whatever has focus, so only run it while nobody is typing.
Add-Type @"
using System; using System.Runtime.InteropServices;
public class SK {
  [StructLayout(LayoutKind.Sequential)] public struct KI { public ushort wVk; public ushort wScan; public uint dwFlags; public uint time; public IntPtr extra; }
  [StructLayout(LayoutKind.Sequential)] public struct INPUT { public uint type; public KI ki; public long pad; }
  [DllImport("user32.dll", SetLastError=true)] public static extern uint SendInput(uint n, INPUT[] i, int size);
  public static void Key(ushort scan, bool up) {
    var i = new INPUT[1]; i[0].type = 1; i[0].ki.wScan = scan; i[0].ki.dwFlags = 0x0008 | (up ? 0x0002u : 0u);
    SendInput(1, i, Marshal.SizeOf(typeof(INPUT))); } }
"@
if (-not $NoFocus) { & (Join-Path $PSScriptRoot "focus_game.ps1") | Out-Null; Start-Sleep -Milliseconds 300 }
[SK]::Key([uint16]$Scan, $false)
Start-Sleep -Milliseconds $HoldMs
[SK]::Key([uint16]$Scan, $true)
"sent scan 0x{0:X}" -f $Scan
