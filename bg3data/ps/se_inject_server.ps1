# Persistent SE console injector (2026-10-02): compiles ConInject once, then serves one JSON request per stdin line
# {"pid": <bg3 pid>, "lines": [...], "gap": <ms between lines>} and answers one line ("ok, ..." or an error).
# Saves ~2 s per eval_lua call over starting powershell.exe + Add-Type every time (se_inject.ps1, kept as fallback).
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class ConInject {
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool FreeConsole();
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool AttachConsole(uint pid);
    [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)]
    static extern IntPtr CreateFile(string name, uint access, uint share, IntPtr sec, uint disp, uint flags, IntPtr tmpl);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool CloseHandle(IntPtr h);
    [StructLayout(LayoutKind.Explicit, CharSet=CharSet.Unicode)]
    struct KEY_EVENT_RECORD {
        [FieldOffset(0)] public int bKeyDown; [FieldOffset(4)] public ushort wRepeatCount;
        [FieldOffset(6)] public ushort wVirtualKeyCode; [FieldOffset(8)] public ushort wVirtualScanCode;
        [FieldOffset(10)] public char UnicodeChar; [FieldOffset(12)] public uint dwControlKeyState;
    }
    [StructLayout(LayoutKind.Explicit)]
    struct INPUT_RECORD { [FieldOffset(0)] public ushort EventType; [FieldOffset(4)] public KEY_EVENT_RECORD Key; }
    [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)]
    static extern bool WriteConsoleInput(IntPtr h, INPUT_RECORD[] buf, uint len, out uint written);

    public static string Send(uint pid, string[] lines, int gapMs) {
        FreeConsole();
        if (!AttachConsole(pid)) return "AttachConsole failed: " + Marshal.GetLastWin32Error();
        IntPtr h = CreateFile("CONIN$", 0xC0000000, 3, IntPtr.Zero, 3, 0, IntPtr.Zero);
        if (h == new IntPtr(-1)) return "CONIN$ failed: " + Marshal.GetLastWin32Error();
        int total = 0;
        foreach (var line in lines) {
            string s = line + "\r";
            var recs = new INPUT_RECORD[s.Length * 2];
            for (int i = 0; i < s.Length; i++) {
                ushort vk = s[i] == '\r' ? (ushort)0x0D : (ushort)0;
                recs[2*i].EventType = 1; recs[2*i].Key.bKeyDown = 1; recs[2*i].Key.wRepeatCount = 1;
                recs[2*i].Key.UnicodeChar = s[i]; recs[2*i].Key.wVirtualKeyCode = vk;
                recs[2*i+1] = recs[2*i]; recs[2*i+1].Key.bKeyDown = 0;
            }
            uint w; WriteConsoleInput(h, recs, (uint)recs.Length, out w); total += (int)w;
            System.Threading.Thread.Sleep(gapMs);
        }
        CloseHandle(h); FreeConsole();
        return "ok, records written: " + total;
    }
}
"@
[Console]::Out.WriteLine("ready"); [Console]::Out.Flush()
while ($true) {
    $req = [Console]::In.ReadLine()
    if ($req -eq $null) { break }
    try {
        $o = $req | ConvertFrom-Json
        $r = [ConInject]::Send([uint32]$o.pid, [string[]]$o.lines, [int]$o.gap)
    } catch { $r = "error: " + $_.Exception.Message }
    [Console]::Out.WriteLine(($r -replace "`r|`n", " ")); [Console]::Out.Flush()
}
