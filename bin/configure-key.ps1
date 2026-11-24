#requires -Version 5.1
param([string]$Python = 'python.exe')

$ErrorActionPreference = 'Stop'
$secure = $null
$bstr = [IntPtr]::Zero
$bytes = $null
$process = $null
$code = 2
$sent = $false

try {
    $start = [Diagnostics.ProcessStartInfo]::new()
    if ($Python.Contains('\') -or $Python.Contains('/')) {
        $start.FileName = (Resolve-Path -LiteralPath $Python).ProviderPath
    }
    else {
        $executable = Get-Command -Name $Python -CommandType Application | Select-Object -First 1
        $start.FileName = $executable.Path
    }
    $writer = Join-Path $PSScriptRoot 'configure_key.py'
    $start.Arguments = '"' + $writer + '" --stdin'
    $start.UseShellExecute = $false
    $start.RedirectStandardInput = $true

    $secure = Read-Host -Prompt 'Docs API Key' -AsSecureString
    if ($secure.Length -lt 16 -or $secure.Length -gt 505) {
        throw 'Invalid API Key length.'
    }
    $bytes = [byte[]]::new($secure.Length)
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    for ($index = 0; $index -lt $bytes.Length; $index++) {
        $character = [Runtime.InteropServices.Marshal]::ReadInt16($bstr, 2 * $index)
        if ($character -lt 33 -or $character -gt 126) {
            throw 'Invalid API Key format.'
        }
        $bytes[$index] = [byte]$character
    }

    # .NET Framework initializes the pipe's StreamWriter with this encoding.
    # Prevent it from writing a preamble before the key bytes.
    $inputEncoding = [Console]::InputEncoding
    try {
        if ($inputEncoding.GetPreamble().Length -ne 0) {
            [Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
        }
        $process = [Diagnostics.Process]::Start($start)
    }
    finally {
        if ($inputEncoding.GetPreamble().Length -ne 0) {
            [Console]::InputEncoding = $inputEncoding
        }
    }
    $process.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length)
    $process.StandardInput.Close()
    $sent = $true
}
catch {
    [Console]::Error.WriteLine('Unable to supply the API Key to the credential writer.')
}
finally {
    $character = 0
    if ($bytes) {
        [Array]::Clear($bytes, 0, $bytes.Length)
    }
    $bytes = $null
    if ($bstr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
    $bstr = [IntPtr]::Zero
    if ($secure) {
        $secure.Dispose()
    }
    $secure = $null
}

if ($process) {
    try {
        if ($sent) {
            $process.WaitForExit()
            $code = $process.ExitCode
        }
        elseif (-not $process.HasExited) {
            $process.Kill()
        }
    }
    catch {
        [Console]::Error.WriteLine('Unable to finish credential configuration.')
    }
    finally {
        $process.Dispose()
    }
}
exit $code
