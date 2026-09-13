[CmdletBinding()]
param(
    [switch]$Yes,
    [switch]$DryRun,
    [string]$PluginDir
)

$ErrorActionPreference = 'Stop'
$python = $env:DCC_MCP_INSTALL_PYTHON
if (-not $python) {
    $command = Get-Command python -ErrorAction Stop
    $python = $command.Source
}
$installArguments = @((Join-Path $PSScriptRoot 'install.py'))
if ($Yes) { $installArguments += '--yes' }
if ($DryRun) { $installArguments += '--dry-run' }
if ($PluginDir) { $installArguments += @('--plugin-dir', $PluginDir) }
& $python @installArguments
exit $LASTEXITCODE
