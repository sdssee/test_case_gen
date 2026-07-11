param(
  [ValidateSet("Fast", "Full")]
  [string]$Mode = "Full"
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$python = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

if (-not (Test-Path $python)) {
  $python = "python"
}

& $python (Join-Path $scriptDir "run-validation.py") --mode $Mode.ToLowerInvariant()
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}
