$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$python = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

if (-not (Test-Path $python)) {
  $python = "python"
}

& $python (Join-Path $scriptDir "validate-test-design.py")
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}

& $python (Join-Path $scriptDir "sync-rule-entrypoints.py")
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}

& $python -m unittest discover -s (Join-Path $repoRoot "tests") -v
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}
