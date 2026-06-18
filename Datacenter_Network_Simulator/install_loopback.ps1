# ============================================================================
#  install_loopback.ps1
#  Ensures the "Microsoft KM-TEST Loopback Adapter" exists and is enabled.
#
#  The simulator binds the simulated device IPs (via AddIPAddress) on a real
#  network adapter; the KM-TEST loopback adapter is what hosts them. Windows
#  ships the driver (inf\netloop.inf, hardware id *MSLOOP) but has NO built-in
#  command to CREATE the device node -- only devcon.exe or the raw SetupAPI
#  can. This script does the SetupAPI calls directly so no external binary is
#  needed:
#     SetupDiCreateDeviceInfoList / SetupDiCreateDeviceInfo
#     SetupDiSetDeviceRegistryProperty (SPDRP_HARDWAREID = *MSLOOP)
#     SetupDiCallClassInstaller (DIF_REGISTERDEVICE)
#     UpdateDriverForPlugAndPlayDevices (binds netloop.inf to the new node)
#
#  Must be run elevated (run.bat self-elevates before calling this).
# ============================================================================

$ErrorActionPreference = 'Stop'

# ---- Require Administrator ----
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "ERROR: must run as Administrator." -ForegroundColor Red
    exit 1
}

function Get-LoopbackAdapter {
    Get-NetAdapter -IncludeHidden -ErrorAction SilentlyContinue |
        Where-Object { $_.InterfaceDescription -like '*KM-TEST Loopback Adapter*' }
}

function Enable-LoopbackAdapter {
    $a = Get-LoopbackAdapter
    if (-not $a) { return $false }
    foreach ($n in $a) {
        if ($n.Status -ne 'Up') {
            try {
                Enable-NetAdapter -Name $n.Name -Confirm:$false -ErrorAction Stop
                Write-Host ("Enabled: " + $n.Name)
            } catch {
                Write-Host ("WARNING: could not enable " + $n.Name + " - " + $_.Exception.Message) -ForegroundColor Yellow
            }
        } else {
            Write-Host ("Already enabled: " + $n.Name)
        }
    }
    return $true
}

# ---- Already installed? just make sure it is enabled ----
if (Get-LoopbackAdapter) {
    Write-Host "KM-TEST Loopback Adapter already installed."
    Enable-LoopbackAdapter | Out-Null
    exit 0
}

Write-Host "KM-TEST Loopback Adapter not found - installing..."

$inf = Join-Path $env:windir 'inf\netloop.inf'
if (-not (Test-Path $inf)) {
    Write-Host "ERROR: $inf not found; cannot install loopback adapter." -ForegroundColor Red
    exit 1
}

# ---- SetupAPI / newdev P/Invoke ----
$signature = @'
using System;
using System.Runtime.InteropServices;
using System.Text;

public static class LoopbackInstaller
{
    [StructLayout(LayoutKind.Sequential)]
    public struct SP_DEVINFO_DATA
    {
        public uint cbSize;
        public Guid ClassGuid;
        public uint DevInst;
        public IntPtr Reserved;
    }

    const int DICD_GENERATE_ID   = 0x00000001;
    const int SPDRP_HARDWAREID    = 0x00000001;
    const int DIF_REGISTERDEVICE  = 0x00000019;
    const int DIF_REMOVE          = 0x00000005;
    const int INSTALLFLAG_FORCE   = 0x00000001;

    [DllImport("setupapi.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr SetupDiCreateDeviceInfoList(ref Guid ClassGuid, IntPtr hwndParent);

    [DllImport("setupapi.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool SetupDiCreateDeviceInfo(IntPtr DeviceInfoSet, string DeviceName,
        ref Guid ClassGuid, string DeviceDescription, IntPtr hwndParent, int CreationFlags,
        ref SP_DEVINFO_DATA DeviceInfoData);

    [DllImport("setupapi.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool SetupDiSetDeviceRegistryProperty(IntPtr DeviceInfoSet,
        ref SP_DEVINFO_DATA DeviceInfoData, int Property, byte[] PropertyBuffer, int PropertyBufferSize);

    [DllImport("setupapi.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool SetupDiCallClassInstaller(int InstallFunction, IntPtr DeviceInfoSet,
        ref SP_DEVINFO_DATA DeviceInfoData);

    [DllImport("setupapi.dll", SetLastError = true)]
    static extern bool SetupDiDestroyDeviceInfoList(IntPtr DeviceInfoSet);

    [DllImport("newdev.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool UpdateDriverForPlugAndPlayDevices(IntPtr hwndParent, string HardwareId,
        string FullInfPath, int InstallFlags, out bool bRebootRequired);

    // Net device setup class
    static Guid GUID_DEVCLASS_NET = new Guid("4D36E972-E325-11CE-BFC1-08002BE10318");
    const string HARDWARE_ID = "*MSLOOP";

    public static void Install(string infPath)
    {
        IntPtr devInfoSet = SetupDiCreateDeviceInfoList(ref GUID_DEVCLASS_NET, IntPtr.Zero);
        if (devInfoSet == IntPtr.Zero || devInfoSet == new IntPtr(-1))
            throw new Exception("SetupDiCreateDeviceInfoList failed: " + Marshal.GetLastWin32Error());

        try
        {
            SP_DEVINFO_DATA devInfo = new SP_DEVINFO_DATA();
            devInfo.cbSize = (uint)Marshal.SizeOf(typeof(SP_DEVINFO_DATA));

            if (!SetupDiCreateDeviceInfo(devInfoSet, "NET", ref GUID_DEVCLASS_NET, null,
                    IntPtr.Zero, DICD_GENERATE_ID, ref devInfo))
                throw new Exception("SetupDiCreateDeviceInfo failed: " + Marshal.GetLastWin32Error());

            // REG_MULTI_SZ: hardware id + double null terminator
            byte[] hwId = Encoding.Unicode.GetBytes(HARDWARE_ID + "\0\0");
            if (!SetupDiSetDeviceRegistryProperty(devInfoSet, ref devInfo, SPDRP_HARDWAREID,
                    hwId, hwId.Length))
                throw new Exception("SetupDiSetDeviceRegistryProperty failed: " + Marshal.GetLastWin32Error());

            if (!SetupDiCallClassInstaller(DIF_REGISTERDEVICE, devInfoSet, ref devInfo))
                throw new Exception("SetupDiCallClassInstaller(REGISTERDEVICE) failed: " + Marshal.GetLastWin32Error());

            bool reboot;
            if (!UpdateDriverForPlugAndPlayDevices(IntPtr.Zero, HARDWARE_ID, infPath, INSTALLFLAG_FORCE, out reboot))
            {
                int err = Marshal.GetLastWin32Error();
                // roll back the half-created node so we do not leave a phantom device
                SetupDiCallClassInstaller(DIF_REMOVE, devInfoSet, ref devInfo);
                throw new Exception("UpdateDriverForPlugAndPlayDevices failed: " + err);
            }
        }
        finally
        {
            SetupDiDestroyDeviceInfoList(devInfoSet);
        }
    }
}
'@

Add-Type -TypeDefinition $signature -Language CSharp

try {
    [LoopbackInstaller]::Install($inf)
} catch {
    Write-Host ("ERROR: loopback install failed - " + $_.Exception.Message) -ForegroundColor Red
    Write-Host "Fallback: install manually via Device Manager > Action > Add legacy hardware" -ForegroundColor Yellow
    Write-Host "  > Network adapters > Microsoft > Microsoft KM-TEST Loopback Adapter." -ForegroundColor Yellow
    exit 1
}

# ---- Give PnP a moment, then confirm + enable ----
Start-Sleep -Seconds 2
if (Get-LoopbackAdapter) {
    Write-Host "KM-TEST Loopback Adapter installed." -ForegroundColor Green
    Enable-LoopbackAdapter | Out-Null
    exit 0
} else {
    Write-Host "WARNING: install reported success but adapter not visible yet." -ForegroundColor Yellow
    exit 1
}