param([string]$LinesFile, [int]$ProcId)
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

    public static string Send(uint pid, string[] lines) {
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
            System.Threading.Thread.Sleep(800);
        }
        CloseHandle(h); FreeConsole();
        return "ok, records written: " + total;
    }
}
"@
$lines = Get-Content -LiteralPath $LinesFile | Where-Object { $_.Trim() -ne '' }
[ConInject]::Send([uint32]$ProcId, [string[]]$lines)
