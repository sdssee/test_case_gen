param(
  [Parameter(Mandatory = $true)]
  [string]$Path
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$targetPath = $Path
if (-not [System.IO.Path]::IsPathRooted($targetPath)) {
  $targetPath = Join-Path $repoRoot $targetPath
}

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
  $python = Get-Command py -ErrorAction SilentlyContinue
}
if (-not $python) {
  throw "Python was not found in PATH."
}

& $python.Source (Join-Path $scriptDir "validate-generated-python-scripts.py") --path $targetPath
exit $LASTEXITCODE
