$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent (Split-Path -Parent $scriptDir)
$runner = Join-Path $repoRoot "scripts\run-test-design.ps1"
$productMap = Join-Path $repoRoot "docs\test-assets\product-map.xlsx"

if (-not (Test-Path -LiteralPath $productMap)) {
  throw "Product map not found for asset migration: $productMap"
}

& powershell -ExecutionPolicy Bypass -File $runner migrate-product-facts --product-map $productMap
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}

& powershell -ExecutionPolicy Bypass -File $runner validate-product-facts --product-map $productMap
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}

Write-Host "Migrated product facts from asset schema 1.0.0 to 2.0.0."
