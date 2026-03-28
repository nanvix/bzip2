# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

# Thin wrapper that delegates to the nanvix-zutil CLI.
# Requires nanvix-zutil to be installed (pip install nanvix-zutil).

param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args
)

$ErrorActionPreference = 'Stop'

& nanvix-zutil @Args
exit $LASTEXITCODE
