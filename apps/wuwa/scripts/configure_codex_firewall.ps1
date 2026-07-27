param(
    [switch]$SkipPromptClose
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )
}

if (-not (Test-IsAdministrator)) {
    throw "configure_codex_firewall.ps1 must run as administrator"
}

if (-not ("Hcid274.AppContainerSid" -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;

namespace Hcid274 {
    public static class AppContainerSid {
        [DllImport("userenv.dll", CharSet = CharSet.Unicode)]
        private static extern int DeriveAppContainerSidFromAppContainerName(
            string name,
            out IntPtr sid);

        [DllImport("advapi32.dll", EntryPoint = "ConvertSidToStringSidW",
            CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool ConvertSidToStringSid(
            IntPtr sid,
            out IntPtr text);

        [DllImport("advapi32.dll")]
        private static extern IntPtr FreeSid(IntPtr sid);

        [DllImport("kernel32.dll")]
        private static extern IntPtr LocalFree(IntPtr value);

        public static string Derive(string packageFamilyName) {
            IntPtr sid;
            int result = DeriveAppContainerSidFromAppContainerName(
                packageFamilyName,
                out sid);
            if (result != 0) {
                Marshal.ThrowExceptionForHR(result);
            }
            try {
                IntPtr text;
                if (!ConvertSidToStringSid(sid, out text)) {
                    throw new Win32Exception();
                }
                try {
                    return Marshal.PtrToStringUni(text);
                }
                finally {
                    LocalFree(text);
                }
            }
            finally {
                FreeSid(sid);
            }
        }
    }

    public static class WindowCloser {
        private delegate bool EnumWindowsProc(IntPtr hwnd, IntPtr lParam);

        [DllImport("user32.dll")]
        private static extern bool EnumWindows(EnumWindowsProc callback, IntPtr lParam);

        [DllImport("user32.dll")]
        private static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);

        [DllImport("user32.dll")]
        private static extern bool IsWindowVisible(IntPtr hwnd);

        [DllImport("user32.dll")]
        private static extern bool PostMessage(IntPtr hwnd, uint message, IntPtr wParam, IntPtr lParam);

        public static int CloseVisibleWindows(uint processId) {
            const uint WM_CLOSE = 0x0010;
            int closed = 0;
            EnumWindows(delegate(IntPtr hwnd, IntPtr lParam) {
                uint owner;
                GetWindowThreadProcessId(hwnd, out owner);
                if (owner == processId && IsWindowVisible(hwnd)) {
                    if (PostMessage(hwnd, WM_CLOSE, IntPtr.Zero, IntPtr.Zero)) {
                        closed++;
                    }
                }
                return true;
            }, IntPtr.Zero);
            return closed;
        }
    }
}
'@
}

$package = Get-AppxPackage -Name "OpenAI.Codex" -ErrorAction SilentlyContinue
if (-not $package) {
    Write-Host "Codex package is not installed; firewall setup skipped"
    exit 0
}

$packageSid = [Hcid274.AppContainerSid]::Derive($package.PackageFamilyName)
$ruleName = "HCID274_Codex_Inbound_Block"
$existing = Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue

if ($existing) {
    $filter = $existing | Get-NetFirewallApplicationFilter
    if ($filter.Package -ne $packageSid) {
        Remove-NetFirewallRule -Name $ruleName
        $existing = $null
    }
}

if (-not $existing) {
    New-NetFirewallRule `
        -Name $ruleName `
        -DisplayName "HCID274 Codex inbound block" `
        -Description "Prevent Codex inbound firewall prompts from blocking unattended game automation" `
        -Group "HCID274 Game Automation" `
        -Direction Inbound `
        -Action Block `
        -Profile Private,Public `
        -Protocol Any `
        -Package $packageSid | Out-Null
}
else {
    Set-NetFirewallRule `
        -Name $ruleName `
        -Enabled True `
        -Direction Inbound `
        -Action Block `
        -Profile Private,Public | Out-Null
}

Write-Host "Codex inbound firewall rule ready: $ruleName ($packageSid)"

if (-not $SkipPromptClose) {
    $pickerHosts = @(
        Get-CimInstance Win32_Process -Filter "Name='PickerHost.exe'" |
            Where-Object {
                $_.CommandLine -like "*FirewallNotificationDialogServer*"
            }
    )
    if ($pickerHosts.Count -eq 1) {
        $closed = [Hcid274.WindowCloser]::CloseVisibleWindows(
            [uint32]$pickerHosts[0].ProcessId
        )
        if ($closed -gt 0) {
            Start-Sleep -Milliseconds 500
            Write-Host "closed the existing Windows firewall prompt without granting inbound access"
        }
        $stalePicker = Get-CimInstance Win32_Process `
            -Filter "ProcessId=$($pickerHosts[0].ProcessId)" `
            -ErrorAction SilentlyContinue
        if (
            $stalePicker -and
            $stalePicker.CommandLine -like "*FirewallNotificationDialogServer*"
        ) {
            Stop-Process -Id $stalePicker.ProcessId -Force
            Write-Host "terminated the closed firewall notification host"
        }
    }
    elseif ($pickerHosts.Count -gt 1) {
        Write-Warning "multiple firewall prompts are open; none was closed automatically"
    }
}
