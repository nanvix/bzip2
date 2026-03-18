# Bootstrap wrapper for Nanvix build scripts.
# Modeled on https://github.com/rust-lang/rust/blob/main/x.ps1

# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

# If script execution is disabled, run:
#   Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

#Requires -Version 5.1

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$MIN_MAJOR = 3
$MIN_MINOR = 12

function Find-Python {
    $candidates = @("py", "python3", "python")
    foreach ($candidate in $candidates) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($null -eq $cmd) { continue }

        # For 'py', pass -3 to select Python 3.
        $pyArgs = if ($candidate -eq "py") { @("-3", "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')") } `
                  else { @("-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')") }

        try {
            $version = & $candidate @pyArgs 2>$null
            if ($LASTEXITCODE -ne 0) { continue }
            $parts = $version.Trim().Split(".")
            $major = [int]$parts[0]
            $minor = [int]$parts[1]
            if ($major -gt $MIN_MAJOR -or ($major -eq $MIN_MAJOR -and $minor -ge $MIN_MINOR)) {
                return $candidate
            }
        } catch {
            continue
        }
    }
    return $null
}

$python = Find-Python
if ($null -eq $python) {
    Write-Error "error: Python ${MIN_MAJOR}.${MIN_MINOR}+ not found in PATH."
    Write-Host "hint:  Install Python 3.12+ and ensure it is on your PATH." -ForegroundColor Yellow
    exit 3
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$zScript = Join-Path $scriptDir ".nanvix\z.py"

if (-not (Test-Path $zScript)) {
    Write-Error "error: $zScript not found."
    Write-Host "hint:  Create .nanvix/z.py with a ZScript subclass." -ForegroundColor Yellow
    exit 3
}

$pyArgs = if ($python -eq "py") { @("-3", $zScript) + $args } else { @($zScript) + $args }
& $python @pyArgs
exit $LASTEXITCODE
