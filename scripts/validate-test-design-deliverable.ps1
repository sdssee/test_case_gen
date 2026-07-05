param(
  [Parameter(Mandatory = $true)]
  [string]$WorkbookPath,

  [string]$BatchStatusPath,

  [string]$ProductMapPath,

  [string]$PageDiscoveryPath,

  [string]$ImportWorkbookPath
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
  if (-not $PageDiscoveryPath) {
    $candidatePageDiscovery = Join-Path (Split-Path -Parent $BatchStatusPath) "page-discovery.csv"
    $PageDiscoveryPath = $candidatePageDiscovery
  }
}
if ($PageDiscoveryPath -and -not $ProductMapPath) {
  $ProductMapPath = Join-Path (Split-Path -Parent $scriptDir) "docs\test-assets\product-map.xlsx"
}
if ($ProductMapPath) {
  $argsList += @("--product-map", $ProductMapPath)
}
if ($PageDiscoveryPath) {
  $argsList += @("--page-discovery", $PageDiscoveryPath)
}
if ($ImportWorkbookPath) {
  $argsList += @("--import-workbook", $ImportWorkbookPath)
}

& $python @argsList
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}
