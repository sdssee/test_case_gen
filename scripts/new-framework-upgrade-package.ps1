param(
  [string]$OutputDir = "dist"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$versionFile = Join-Path $repoRoot "VERSION"
$manifest = Join-Path $repoRoot "UPGRADE_MANIFEST.md"
$removalManifestName = "FRAMEWORK_REMOVALS.json"
$deprecatedFiles = @(
  ".codebuddy/agents/test-delivery.md"
)

if (-not (Test-Path $versionFile)) {
  throw "Missing VERSION file."
}
if (-not (Test-Path $manifest)) {
  throw "Missing UPGRADE_MANIFEST.md."
}

$frameworkVersion = (Get-Content -Encoding utf8 $versionFile | ForEach-Object { $_.TrimStart([char]0xFEFF) } | Where-Object { $_ -match "^framework_version=" }) -replace "^framework_version=", ""
if (-not $frameworkVersion) {
  throw "VERSION is missing framework_version."
}
$assetSchemaVersion = (Get-Content -Encoding utf8 $versionFile | ForEach-Object { $_.TrimStart([char]0xFEFF) } | Where-Object { $_ -match "^asset_schema_version=" }) -replace "^asset_schema_version=", ""
if (-not $assetSchemaVersion) {
  throw "VERSION is missing asset_schema_version."
}

$outputPath = Join-Path $repoRoot $OutputDir
New-Item -ItemType Directory -Force -Path $outputPath | Out-Null
$packagePath = Join-Path $outputPath "framework-upgrade-$frameworkVersion.zip"
if (Test-Path $packagePath) {
  Remove-Item -LiteralPath $packagePath -Force
}

$includeFiles = @(
  "AGENTS.md",
  "CODEBUDDY.md",
  "README.md",
  "README_IMPORT.md",
  ".codebuddy/settings.json",
  ".codebuddy/hooks/guard-agent-tool.py",
  ".codebuddy/hooks/record-page-probe.py",
  "requirements.txt",
  "pyproject.toml",
  "VERSION",
  "UPGRADE_MANIFEST.md",
  "docs/ARCHITECTURE.md",
  "docs/AGENT_ORCHESTRATION.md",
  "docs/CODEBUDDY_AGENT_ADAPTER.md",
  "docs/RULE_OWNERSHIP.md",
  "docs/UPGRADE.md",
  "docs/test-assets/README.md",
  "docs/test-assets/batch-runs/README.md",
  "scripts/test_design/orchestration/execution_binding.py",
  "tests/test_codebuddy_agent_guard.py",
  "tests/test_codebuddy_page_probe_recorder.py",
  "tests/test_page_probe_receipts.py"
)

$includeDirs = @(
  ".github",
  ".codebuddy",
  "scripts",
  "tests",
  "docs/test-design/rules",
  "docs/test-design/schemas",
  "docs/test-assets/batch-runs/templates"
)

$includeGlobs = @(
  "docs/test-design/*.md",
  "docs/test-design/*.xlsx"
)

$protectedPrefixes = @(
  "docs/test-assets/product-map.xlsx",
  "docs/test-assets/modules/",
  "docs/test-assets/imports/",
  "docs/test-assets/indexes/",
  "docs/test-design/current/",
  "docs/test-design/deliverables/"
)
# PROTECTED_ASSET_DIRS: docs/test-assets/, docs/test-design/current/, docs/test-design/deliverables/

function Test-ProtectedPath {
  param([string]$RelativePath)
  $normalized = $RelativePath.Replace("\", "/")
  foreach ($prefix in $protectedPrefixes) {
    if ($normalized.StartsWith($prefix)) {
      return $true
    }
  }
  return $false
}

function Test-GeneratedPath {
  param([string]$RelativePath)
  $normalized = $RelativePath.Replace("\", "/")
  if ($normalized -match "(^|/)__pycache__/" -or $normalized -match "\.pyc$") {
    return $true
  }
  return $false
}

function Test-DeprecatedPath {
  param([string]$RelativePath)
  $normalized = $RelativePath.Replace("\", "/").TrimStart("/")
  return $deprecatedFiles -contains $normalized
}

function Get-RelativePath {
  param(
    [string]$BasePath,
    [string]$FullPath
  )
  $base = (Resolve-Path -LiteralPath $BasePath).Path.TrimEnd("\") + "\"
  $full = (Resolve-Path -LiteralPath $FullPath).Path
  if (-not $full.StartsWith($base, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Path is outside base path: $FullPath"
  }
  return $full.Substring($base.Length)
}

$files = New-Object System.Collections.Generic.List[string]
foreach ($file in $includeFiles) {
  $absolute = Join-Path $repoRoot $file
  if (Test-Path $absolute) {
    $files.Add($file.Replace("/", "\"))
  }
}

foreach ($dir in $includeDirs) {
  $absoluteDir = Join-Path $repoRoot $dir
  if (Test-Path $absoluteDir) {
    Get-ChildItem -Path $absoluteDir -Recurse -File | ForEach-Object {
      $relative = Get-RelativePath -BasePath $repoRoot -FullPath $_.FullName
      if (-not (Test-ProtectedPath $relative) -and -not (Test-GeneratedPath $relative) -and -not (Test-DeprecatedPath $relative)) {
        $files.Add($relative)
      }
    }
  }
}

foreach ($glob in $includeGlobs) {
  Get-ChildItem -Path (Join-Path $repoRoot $glob) -File | ForEach-Object {
    $relative = Get-RelativePath -BasePath $repoRoot -FullPath $_.FullName
    if (-not (Test-ProtectedPath $relative) -and -not (Test-GeneratedPath $relative) -and -not (Test-DeprecatedPath $relative)) {
      $files.Add($relative)
    }
  }
}

$uniqueFiles = $files | Sort-Object -Unique
if (-not $uniqueFiles) {
  throw "No framework files found to package."
}

$staging = Join-Path $env:TEMP ("test-case-gen-upgrade-" + [System.Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $staging | Out-Null
try {
  foreach ($relative in $uniqueFiles) {
    $source = Join-Path $repoRoot $relative
    $target = Join-Path $staging $relative
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
    Copy-Item -LiteralPath $source -Destination $target -Force
  }
  $removalManifest = [ordered]@{
    schema_version = "1.0.0"
    remove_files = @($deprecatedFiles)
  } | ConvertTo-Json -Depth 10
  [System.IO.File]::WriteAllText(
    (Join-Path $staging $removalManifestName),
    $removalManifest + [Environment]::NewLine,
    [System.Text.UTF8Encoding]::new($false)
  )
  Compress-Archive -Path (Join-Path $staging "*") -DestinationPath $packagePath -Force
}
finally {
  if (Test-Path $staging) {
    Remove-Item -LiteralPath $staging -Recurse -Force
  }
}

Write-Host "Created framework upgrade package: $packagePath"
Write-Host "framework_version=$frameworkVersion asset_schema_version=$assetSchemaVersion"
Write-Host "Protected asset directories were excluded. PROTECTED_ASSET_DIRS"
