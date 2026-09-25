$ErrorActionPreference = 'Stop'

function Remove-RdstCredentials {
    Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class RdstCredentials {
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct Credential {
        public uint Flags, Type;
        public string TargetName, Comment;
        public long LastWritten;
        public uint CredentialBlobSize;
        public IntPtr CredentialBlob;
        public uint Persist, AttributeCount;
        public IntPtr Attributes;
        public string TargetAlias, UserName;
    }
    [DllImport("advapi32.dll", EntryPoint="CredEnumerateW", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern bool Enumerate(IntPtr filter, uint flags, out uint count, out IntPtr credentials);
    [DllImport("advapi32.dll", EntryPoint="CredDeleteW", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern bool Delete(string target, uint type, uint flags);
    [DllImport("advapi32.dll")]
    public static extern void CredFree(IntPtr buffer);
}
'@

    # Pass a native NULL filter to enumerate every target in the current user's vault.
    $count = [uint32]0
    $credentials = [IntPtr]::Zero
    if (-not [RdstCredentials]::Enumerate([IntPtr]::Zero, 0, [ref]$count, [ref]$credentials)) {
        $code = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        if ($code -ne 1168) { throw "CredEnumerate failed: $code" }
    } else {
        try {
            for ($i = 0; $i -lt $count; $i++) {
                $pointer = [Runtime.InteropServices.Marshal]::ReadIntPtr($credentials, $i * [IntPtr]::Size)
                $credential = [Runtime.InteropServices.Marshal]::PtrToStructure($pointer, [type][RdstCredentials+Credential])
                if ($credential.Type -eq 1 -and $credential.TargetName -cmatch '^(?:rdst-(?:web|slack)(?:@.+)?|.+@rdst-(?:web|slack))$') {
                    if (-not [RdstCredentials]::Delete($credential.TargetName, 1, 0)) {
                        throw "CredDelete failed for target $($credential.TargetName): $([Runtime.InteropServices.Marshal]::GetLastWin32Error())"
                    }
                }
            }
        } finally { [RdstCredentials]::CredFree($credentials) }
    }
}

function Remove-RdstOwnedDirectory {
    param([string]$DataDirectory)
    # A custom directory is opt-in owned storage. Unmarked directories are preserved.
    if (-not $DataDirectory) { return }
    $directory = Get-Item -LiteralPath $DataDirectory -ErrorAction SilentlyContinue
    if (-not $directory -or -not $directory.PSIsContainer -or -not $directory.Parent) { return }
    $marker = Join-Path $directory.FullName '.rdst-owned'
    if (-not (Test-Path -LiteralPath $marker -PathType Leaf)) { return }
    if ((Get-Content -LiteralPath $marker -Raw) -ceq "rdst-desktop-data-v1`n") {
        $links = @($directory) + @(Get-ChildItem -LiteralPath $directory.FullName -Force -Recurse)
        $hasLinks = @($links | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }).Count -gt 0
        if (-not $hasLinks) {
            Remove-Item -LiteralPath $directory.FullName -Recurse -Force
        }
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    Remove-RdstCredentials
    Remove-RdstOwnedDirectory $env:RDST_DATA_DIR
}
