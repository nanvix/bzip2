# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

# Thin wrapper that delegates to the nanvix-zutil CLI.
# Requires nanvix-zutil to be installed (pip install nanvix-zutil).

$ErrorActionPreference = 'Stop'

& nanvix-zutil @args
exit $LASTEXITCODE
