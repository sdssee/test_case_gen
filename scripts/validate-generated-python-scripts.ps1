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

$bundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if (Test-Path -LiteralPath $bundledPython) {
  $pythonPath = $bundledPython
} else {
  $python = Get-Command python -ErrorAction SilentlyContinue
  if (-not $python) {
    $python = Get-Command py -ErrorAction SilentlyContinue
  }
  if (-not $python) {
    throw "Python was not found. Use the bundled workspace Python or provide Python in PATH; do not install or uninstall global dependencies."
  }
  $pythonPath = $python.Source
}

& $pythonPath (Join-Path $scriptDir "validate-generated-python-scripts.py") --path $targetPath
exit $LASTEXITCODE
