param(
  [Parameter(Mandatory = $true)]
  [string]$PackagePath,
  [switch]$RunMigrations
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$resolvedPackage = Resolve-Path -LiteralPath $PackagePath
$timestamp = Get-Date -Format "yyyyMMddHHmmss"
$backupRoot = Join-Path $repoRoot ".upgrade-backups\$timestamp"
$extractRoot = Join-Path $env:TEMP ("test-case-gen-upgrade-apply-" + [System.Guid]::NewGuid().ToString("N"))

$protectedPrefixes = @(
  "docs/test-assets/",
  "docs/test-design/current/",
  "docs/test-design/deliverables/"
)
# PROTECTED_ASSET_DIRS: docs/test-assets/, docs/test-design/current/, docs/test-design/deliverables/
# VERSION keys: framework_version, asset_schema_version

$allowedPrefixes = @(
  ".github/",
  ".codebuddy/",
  "docs/ARCHITECTURE.md",
  "docs/UPGRADE.md",
  "docs/test-design/rules/",
  "docs/test-design/schemas/",
  "docs/test-assets/README.md",
  "docs/test-assets/batch-runs/README.md",
  "docs/test-assets/batch-runs/templates/",
  "scripts/",
  "AGENTS.md",
  "CODEBUDDY.md",
  "README.md",
  "README_IMPORT.md",
  "requirements.txt",
  "pyproject.toml",
  "tests/",
  "VERSION",
  "UPGRADE_MANIFEST.md"
)

function Normalize-RelativePath {
  param([string]$Path)
  return $Path.Replace("\", "/").TrimStart("/")
}

function Test-ProtectedPath {
  param([string]$RelativePath)
  $normalized = Normalize-RelativePath $RelativePath
  if ($normalized -eq "docs/test-assets/README.md" -or
      $normalized -eq "docs/test-assets/batch-runs/README.md" -or
      $normalized.StartsWith("docs/test-assets/batch-runs/templates/")) {
    return $false
  }
  foreach ($prefix in $protectedPrefixes) {
    if ($normalized.StartsWith($prefix)) {
      return $true
    }
  }
  return $false
}

function Test-AllowedPath {
  param([string]$RelativePath)
  $normalized = Normalize-RelativePath $RelativePath
  foreach ($prefix in $allowedPrefixes) {
    if ($normalized -eq $prefix.TrimEnd("/") -or $normalized.StartsWith($prefix)) {
      return $true
    }
  }
  if ($normalized -match "^docs/test-design/[^/]+\.(md|xlsx)$") {
    return $true
  }
  return $false
}

function Read-VersionValue {
  param(
    [string]$File,
    [string]$Key
  )
  if (-not (Test-Path $File)) {
    return ""
  }
  $line = Get-Content -Encoding utf8 $File | ForEach-Object { $_.TrimStart([char]0xFEFF) } | Where-Object { $_ -match "^$Key=" } | Select-Object -First 1
  if (-not $line) {
    return ""
  }
  return $line -replace "^$Key=", ""
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

function Merge-LocalOverrideBlock {
  param(
    [string]$CurrentPath,
    [string]$IncomingPath,
    [string]$RelativePath
  )
  if (-not (Test-Path -LiteralPath $CurrentPath) -or -not (Test-Path -LiteralPath $IncomingPath)) {
    return
  }
  $begin = "<!-- LOCAL-OVERRIDES:BEGIN -->"
  $end = "<!-- LOCAL-OVERRIDES:END -->"
  $current = [System.IO.File]::ReadAllText($CurrentPath)
  $incoming = [System.IO.File]::ReadAllText($IncomingPath)
  if (-not ($current.Contains($begin) -and $current.Contains($end))) {
    throw "Existing $RelativePath has no LOCAL-OVERRIDES block. Upgrade stopped before overwrite; migrate local instructions into the marker block and retry."
  }
  if (-not ($incoming.Contains($begin) -and $incoming.Contains($end))) {
    throw "Incoming $RelativePath has no LOCAL-OVERRIDES block; refusing to overwrite local instructions."
  }
  $pattern = "(?s)(?<=${begin}).*?(?=${end})"
  $localBody = [System.Text.RegularExpressions.Regex]::Match($current, $pattern).Value
  $merged = [System.Text.RegularExpressions.Regex]::Replace($incoming, $pattern, [System.Text.RegularExpressions.MatchEvaluator]{ param($match) $localBody }, 1)
  [System.IO.File]::WriteAllText($IncomingPath, $merged, [System.Text.UTF8Encoding]::new($false))
}

function Restore-UpgradeSnapshot {
  param(
    [string]$RepositoryRoot,
    [string]$SnapshotRoot,
    [System.Collections.Generic.List[string]]$CreatedTargets
  )

  foreach ($target in $CreatedTargets) {
    if (Test-Path -LiteralPath $target) {
      Remove-Item -LiteralPath $target -Force
    }
  }

  $frameworkSnapshot = Join-Path $SnapshotRoot "framework"
  if (Test-Path -LiteralPath $frameworkSnapshot) {
    Get-ChildItem -Path $frameworkSnapshot -Recurse -File | ForEach-Object {
      $relative = Get-RelativePath -BasePath $frameworkSnapshot -FullPath $_.FullName
      $target = Join-Path $RepositoryRoot $relative
      New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
      Copy-Item -LiteralPath $_.FullName -Destination $target -Force
    }
  }

  foreach ($protected in $protectedPrefixes) {
    $snapshot = Join-Path $SnapshotRoot $protected
    if (-not (Test-Path -LiteralPath $snapshot)) {
      continue
    }
    $target = Join-Path $RepositoryRoot $protected
    if (Test-Path -LiteralPath $target) {
      Remove-Item -LiteralPath $target -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
    Copy-Item -LiteralPath $snapshot -Destination $target -Recurse -Force
  }
}

New-Item -ItemType Directory -Force -Path $extractRoot | Out-Null
try {
  Expand-Archive -LiteralPath $resolvedPackage -DestinationPath $extractRoot -Force

  $manifest = Join-Path $extractRoot "UPGRADE_MANIFEST.md"
  $packageVersion = Join-Path $extractRoot "VERSION"
  if (-not (Test-Path $manifest)) {
    throw "Upgrade package is missing UPGRADE_MANIFEST.md."
  }
  if (-not (Test-Path $packageVersion)) {
    throw "Upgrade package is missing VERSION."
  }

  $currentVersion = Join-Path $repoRoot "VERSION"
  $currentAssetSchemaVersion = Read-VersionValue -File $currentVersion -Key "asset_schema_version"
  $packageAssetSchemaVersion = Read-VersionValue -File $packageVersion -Key "asset_schema_version"

  if (-not $currentAssetSchemaVersion) {
    throw "Current VERSION is missing asset_schema_version."
  }
  if (-not $packageAssetSchemaVersion) {
    throw "Package VERSION is missing asset_schema_version."
  }

  $requiresMigration = $currentAssetSchemaVersion -ne $packageAssetSchemaVersion
  $migrationRelativePath = "scripts\migrations\${currentAssetSchemaVersion}_to_${packageAssetSchemaVersion}.ps1"
  $extractedMigration = Join-Path $extractRoot $migrationRelativePath
  $repoMigration = Join-Path $repoRoot $migrationRelativePath
  if ($requiresMigration -and -not $RunMigrations) {
    throw "Asset schema version changed from $currentAssetSchemaVersion to $packageAssetSchemaVersion. No files were copied. Review and run with -RunMigrations after confirming migration script: $migrationRelativePath"
  }
  if ($requiresMigration -and -not (Test-Path $extractedMigration)) {
    throw "Missing migration script in upgrade package: $migrationRelativePath"
  }

  $packageFiles = New-Object System.Collections.Generic.List[object]
  Get-ChildItem -Path $extractRoot -Recurse -File | ForEach-Object {
    $relative = Get-RelativePath -BasePath $extractRoot -FullPath $_.FullName
    $normalized = Normalize-RelativePath $relative
    if (Test-ProtectedPath $normalized) {
      Write-Host "Skip protected asset path: $normalized"
      return
    }
    if (-not (Test-AllowedPath $normalized)) {
      Write-Host "Skip unexpected package path: $normalized"
      return
    }
    $packageFiles.Add([PSCustomObject]@{ Source = $_.FullName; Relative = $relative })
  }

  New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
  foreach ($protected in $protectedPrefixes) {
    $source = Join-Path $repoRoot $protected
    if (Test-Path $source) {
      $target = Join-Path $backupRoot $protected
      New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
      Copy-Item -LiteralPath $source -Destination $target -Recurse -Force
    }
  }

  $frameworkSnapshot = Join-Path $backupRoot "framework"
  $createdTargets = New-Object System.Collections.Generic.List[string]
  foreach ($item in $packageFiles) {
    $target = Join-Path $repoRoot $item.Relative
    if (Test-Path -LiteralPath $target) {
      $snapshot = Join-Path $frameworkSnapshot $item.Relative
      New-Item -ItemType Directory -Force -Path (Split-Path -Parent $snapshot) | Out-Null
      Copy-Item -LiteralPath $target -Destination $snapshot -Force
    }
    else {
      $createdTargets.Add($target)
    }
  }

  try {
    $entryOverridePaths = @(
      "AGENTS.md",
      "CODEBUDDY.md",
      ".codebuddy\skills\test-design\SKILL.md"
    )
    foreach ($relative in $entryOverridePaths) {
      Merge-LocalOverrideBlock -CurrentPath (Join-Path $repoRoot $relative) -IncomingPath (Join-Path $extractRoot $relative) -RelativePath $relative
    }
    foreach ($item in $packageFiles) {
      $target = Join-Path $repoRoot $item.Relative
      New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
      Copy-Item -LiteralPath $item.Source -Destination $target -Force
    }

    if ($requiresMigration) {
      & powershell -ExecutionPolicy Bypass -File $repoMigration
      if ($LASTEXITCODE -ne 0) {
        throw "Asset migration failed with exit code $LASTEXITCODE."
      }
    }

    & powershell -ExecutionPolicy Bypass -File (Join-Path $repoRoot "scripts\validate-test-design.ps1") -Mode Fast
    if ($LASTEXITCODE -ne 0) {
      throw "Framework validation failed with exit code $LASTEXITCODE."
    }
  }
  catch {
    Write-Warning "Upgrade failed; restoring framework and protected assets from $backupRoot"
    Restore-UpgradeSnapshot -RepositoryRoot $repoRoot -SnapshotRoot $backupRoot -CreatedTargets $createdTargets
    throw
  }

  Write-Host "Framework upgrade applied."
  Write-Host "Framework and protected assets backup: $backupRoot"
}
finally {
  if (Test-Path $extractRoot) {
    Remove-Item -LiteralPath $extractRoot -Recurse -Force
  }
}
