using System;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32;

namespace OverCompressingUI.Core;

/// <summary>Move files and folders to the Recycle Bin (never delete for good): refused where it would delete instead
/// (a network, removable or optical drive, a bin set to delete at once, or one too small for them). A port of the
/// Tkinter window's windows_trash.</summary>
public static class Trash
{
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern uint GetDriveTypeW(string root);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool GetVolumeNameForVolumeMountPointW(string mountPoint, StringBuilder volumeName, uint bufferLength);

    [StructLayout(LayoutKind.Sequential)]
    private struct SHQUERYRBINFO
    {
        public uint cbSize;
        public long i64Size;
        public long i64NumItems;
    }

    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    private static extern int SHQueryRecycleBinW(string root, ref SHQUERYRBINFO info);

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]  // natural alignment on x64 (Pack=1 is the 32-bit layout)
    private struct SHFILEOPSTRUCTW
    {
        public IntPtr hwnd;
        public uint wFunc;
        public IntPtr pFrom;
        public IntPtr pTo;
        public ushort fFlags;
        public int fAnyOperationsAborted;
        public IntPtr hNameMappings;
        public IntPtr lpszProgressTitle;
    }

    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    private static extern int SHFileOperationW(ref SHFILEOPSTRUCTW op);

    /// <summary>Move these paths to the Recycle Bin; null, or why not.</summary>
    public static string? ToTrash(string[] paths, long size)
    {
        if (!OperatingSystem.IsWindows())
            return "this system has no Recycle Bin the window knows: use your file manager";
        var root = Path.GetPathRoot(Path.GetFullPath(paths[0])) ?? "";
        if (root.StartsWith(@"\\"))
            return $"{root} is a network folder: it has no Recycle Bin";
        if (GetDriveTypeW(root) != 3)  // DRIVE_FIXED
            return $"the drive {root} has no Recycle Bin";
        var volume = new StringBuilder(64);
        if (GetVolumeNameForVolumeMountPointW(root, volume, 64))
        {
            var guid = volume.ToString().TrimEnd('\\').Split('\\').Last();
            if (guid.StartsWith("Volume"))
                guid = guid["Volume".Length..];
            long? capacity = null;
            try
            {
                using var key = Registry.CurrentUser.OpenSubKey($@"Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket\Volume\{guid}");
                if (key is not null)
                {
                    if (key.GetValue("NukeOnDelete") is int nuke && nuke != 0)
                        return $"the Recycle Bin of {root} is set to delete files at once";
                    if (key.GetValue("MaxCapacity") is int max)
                        capacity = (long)max << 20;
                }
            }
            catch (Exception) { }
            var info = new SHQUERYRBINFO { cbSize = (uint)Marshal.SizeOf<SHQUERYRBINFO>() };
            long used = SHQueryRecycleBinW(root, ref info) == 0 ? info.i64Size : 0;
            if (capacity is not null && used + size > capacity)
                return $"the Recycle Bin of {root} holds {T.SizeText(capacity)} ({T.SizeText(used)} in it now), too little for " +
                       $"{T.SizeText(size)}: empty it, give it more room, or delete them yourself";
        }
        var names = string.Join("\0", paths.Select(Path.GetFullPath)) + "\0\0";
        var from = Marshal.StringToHGlobalUni(names);
        try
        {
            // FO_DELETE; FOF_ALLOWUNDO (to the bin), FOF_NOCONFIRMATION (asked already), FOF_WANTNUKEWARNING
            var op = new SHFILEOPSTRUCTW { wFunc = 3, pFrom = from, fFlags = 0x40 | 0x10 | 0x4000 };
            int code = SHFileOperationW(ref op);
            if (op.fAnyOperationsAborted != 0)
                return "cancelled";
            return code != 0 ? $"Windows reported error 0x{code:x}" : null;
        }
        finally
        {
            Marshal.FreeHGlobal(from);
        }
    }
}
