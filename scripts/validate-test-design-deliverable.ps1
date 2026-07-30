param(
  [Parameter(Mandatory = $true)]
  [string]$WorkbookPath,

  [Parameter(Mandatory = $true)]
  [string]$ImportWorkbookPath,

  [string]$DiscoveryStatePath
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

if (-not (Test-Path $python)) {
  $python = "python"
}

$argsList = @((Join-Path $scriptDir "validate-test-design-deliverable.py"), "--workbook", $WorkbookPath)
$argsList += @("--import-workbook", $ImportWorkbookPath)
if ($DiscoveryStatePath) {
  $argsList += @("--discovery-state", $DiscoveryStatePath)
}

& $python -B @argsList
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}
