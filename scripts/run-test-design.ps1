param(
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$ToolArgs
)

$ErrorActionPreference = "Stop"
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$bundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

$pythonCandidates = @()
if ($env:TEST_DESIGN_PYTHON) {
  $pythonCandidates += $env:TEST_DESIGN_PYTHON
}
if (Test-Path -LiteralPath $bundledPython) {
  $pythonCandidates += $bundledPython
}
$pathPython = Get-Command python -ErrorAction SilentlyContinue
if ($pathPython) {
  $pythonCandidates += $pathPython.Source
}

$python = $null
foreach ($candidate in $pythonCandidates | Select-Object -Unique) {
  & $candidate -c "import openpyxl; assert openpyxl.__version__ == '3.1.5'" 2>$null
  if ($LASTEXITCODE -eq 0) {
    $python = $candidate
    break
  }
}

if (-not $python) {
  throw "No compatible Python runtime found. Install Python 3.11-3.13 and run: python -m pip install -r `"$repoRoot\requirements.txt`", or set TEST_DESIGN_PYTHON."
}

& $python (Join-Path $scriptDir "test_design_cli.py") @ToolArgs
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}
