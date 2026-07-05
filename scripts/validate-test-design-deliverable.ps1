param(
  [Parameter(Mandatory = $true)]
  [string]$WorkbookPath,

  [string]$BatchStatusPath
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

if (-not (Test-Path $python)) {
  $python = "python"
}

$argsList = @((Join-Path $scriptDir "validate-test-design-deliverable.py"), "--workbook", $WorkbookPath)
if ($BatchStatusPath) {
  $argsList += @("--batch-status", $BatchStatusPath)
}

& $python @argsList
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}
