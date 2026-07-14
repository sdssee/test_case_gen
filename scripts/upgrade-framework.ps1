param(
  [Parameter(Mandatory = $true)]
  [string]$PackagePath,
  [switch]$RunMigrations
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$resolvedPackage = Resolve-Path -LiteralPath $PackagePath
$timestamp = (Get-Date -Format "yyyyMMddHHmmssfff") + "-" + [System.Guid]::NewGuid().ToString("N").Substring(0, 8)
$backupRoot = Join-Path $repoRoot ".upgrade-backups\$timestamp"
$extractRoot = Join-Path $env:TEMP ("test-case-gen-upgrade-apply-" + [System.Guid]::NewGuid().ToString("N"))
$removalManifestName = "FRAMEWORK_REMOVALS.json"
$supportedLegacyRemovalPaths = @(
  ".codebuddy/agents/test-delivery.md"
)
$requiredFrameworkFiles = @(
  ".codebuddy/settings.json",
  ".codebuddy/hooks/guard-agent-tool.py",
  ".codebuddy/hooks/record-page-probe.py",
  "scripts/test_design/orchestration/execution_binding.py"
)

$protectedPrefixes = @(
  "docs/test-assets/",
  "docs/test-design/current/",
  "docs/test-design/deliverables/"
)
# PROTECTED_ASSET_DIRS: docs/test-assets/, docs/test-design/current/, docs/test-design/deliverables/
# VERSION keys: framework_version, asset_schema_version

$allowedFiles = @(
  ".codebuddy/settings.json",
  ".codebuddy/hooks/guard-agent-tool.py",
  ".codebuddy/hooks/record-page-probe.py",
  "docs/ARCHITECTURE.md",
  "docs/AGENT_ORCHESTRATION.md",
  "docs/CODEBUDDY_AGENT_ADAPTER.md",
  "docs/RULE_OWNERSHIP.md",
  "docs/UPGRADE.md",
  "docs/test-assets/README.md",
  "docs/test-assets/batch-runs/README.md",
  "scripts/test_design/orchestration/execution_binding.py",
  "AGENTS.md",
  "CODEBUDDY.md",
  "README.md",
  "README_IMPORT.md",
  "requirements.txt",
  "pyproject.toml",
  "tests/test_codebuddy_agent_guard.py",
  "tests/test_codebuddy_page_probe_recorder.py",
  "tests/test_page_probe_receipts.py",
  "FRAMEWORK_REMOVALS.json",
  "VERSION",
  "UPGRADE_MANIFEST.md"
)
$allowedDirectories = @(
  ".github/",
  ".codebuddy/.rules/",
  ".codebuddy/agents/",
  ".codebuddy/commands/",
  ".codebuddy/rules/",
  ".codebuddy/skills/",
  "docs/test-design/rules/",
  "docs/test-design/schemas/",
  "docs/test-assets/batch-runs/templates/",
  "scripts/",
  "tests/"
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
  if (-not $normalized -or $normalized.Contains([char]0) -or $normalized.Contains(":")) {
    return $false
  }
  foreach ($segment in $normalized.Split("/")) {
    if (-not $segment -or $segment -eq "." -or $segment -eq "..") {
      return $false
    }
  }
  foreach ($file in $allowedFiles) {
    if ($normalized -ceq $file) {
      return $true
    }
  }
  foreach ($directory in $allowedDirectories) {
    if ($normalized.StartsWith($directory, [System.StringComparison]::Ordinal)) {
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

function Read-JsonObjectFile {
  param(
    [string]$Path,
    [string]$Label
  )
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    throw "$Label is missing: $Path"
  }
  try {
    $value = Get-Content -LiteralPath $Path -Raw -Encoding utf8 | ConvertFrom-Json -ErrorAction Stop
  }
  catch {
    throw "$Label contains invalid JSON: $($_.Exception.Message)"
  }
  if ($null -eq $value -or $value -is [System.Array] -or $value -is [string] -or $value -is [ValueType]) {
    throw "$Label must contain one JSON object."
  }
  return $value
}

function Get-ObjectPropertyValue {
  param(
    [object]$Object,
    [string]$Name
  )
  if ($null -eq $Object) {
    return $null
  }
  $property = $Object.PSObject.Properties[$Name]
  if ($null -eq $property) {
    return $null
  }
  Write-Output -NoEnumerate $property.Value
}

function Test-IsJsonObject {
  param([object]$Value)
  return $null -ne $Value -and
    -not ($Value -is [System.Array]) -and
    -not ($Value -is [string]) -and
    -not ($Value -is [ValueType])
}

function Get-HookEventEntries {
  param(
    [object]$Hooks,
    [string]$EventName,
    [string]$Label
  )
  $property = $Hooks.PSObject.Properties[$EventName]
  if ($null -eq $property) {
    return @()
  }
  if (-not ($property.Value -is [System.Array])) {
    throw "$Label hooks.$EventName must be a JSON array."
  }
  return @($property.Value)
}

function Get-ShellCommandTokens {
  param([string]$Command)

  $tokens = New-Object System.Collections.Generic.List[string]
  $builder = New-Object System.Text.StringBuilder
  $quote = [char]0
  for ($index = 0; $index -lt $Command.Length; $index++) {
    $character = $Command[$index]
    if ($quote -ne [char]0) {
      if ($character -eq $quote) {
        $quote = [char]0
      }
      elseif (($character -eq "\" -or $character -eq "``") -and
              $index + 1 -lt $Command.Length -and $Command[$index + 1] -eq $quote) {
        $index++
        [void]$builder.Append($Command[$index])
      }
      else {
        [void]$builder.Append($character)
      }
      continue
    }

    if ($character -eq '"' -or $character -eq "'") {
      $quote = $character
      continue
    }
    if ($character -eq "#" -and $builder.Length -eq 0) {
      while ($index + 1 -lt $Command.Length -and $Command[$index + 1] -ne "`n") {
        $index++
      }
      continue
    }
    if ([char]::IsWhiteSpace($character)) {
      if ($builder.Length -gt 0) {
        $tokens.Add($builder.ToString())
        [void]$builder.Clear()
      }
      if ($character -eq "`r" -or $character -eq "`n") {
        $tokens.Add(";")
      }
      continue
    }
    if ($character -eq ";" -or $character -eq "|" -or $character -eq "&" -or
        $character -eq "(" -or $character -eq ")") {
      if ($builder.Length -gt 0) {
        $tokens.Add($builder.ToString())
        [void]$builder.Clear()
      }
      $operator = [string]$character
      if (($character -eq "|" -or $character -eq "&") -and
          $index + 1 -lt $Command.Length -and $Command[$index + 1] -eq $character) {
        $operator += [string]$Command[$index + 1]
        $index++
      }
      $tokens.Add($operator)
      continue
    }
    [void]$builder.Append($character)
  }
  if ($builder.Length -gt 0) {
    $tokens.Add($builder.ToString())
  }
  return @($tokens)
}

function Test-IsFrameworkPythonCommand {
  param(
    [string]$Command,
    [string]$ScriptName
  )

  $tokens = @(Get-ShellCommandTokens -Command $Command)
  $commandPosition = $true
  for ($index = 0; $index -lt $tokens.Count; $index++) {
    $token = [string]$tokens[$index]
    if ($token -in @(";", "|", "||", "&", "&&", "(", ")")) {
      $commandPosition = $true
      continue
    }
    if (-not $commandPosition) {
      continue
    }

    $lower = $token.ToLowerInvariant()
    if ($lower -in @("if", "then", "elif", "else", "do")) {
      continue
    }
    if ($lower -in @("command", "env", "nohup")) {
      continue
    }
    if ($token -match "^[A-Za-z_][A-Za-z0-9_]*=") {
      continue
    }

    $interpreter = [System.IO.Path]::GetFileName($token.Replace("\", "/"))
    if ($interpreter -match "^(?i:python(?:3(?:\.\d+)*)?|py)(?:\.exe)?$") {
      if ($index + 1 -lt $tokens.Count) {
        $candidate = ([string]$tokens[$index + 1]).Replace("\", "/")
        $expected = ".codebuddy/hooks/$ScriptName"
        if ($candidate.Equals($expected, [System.StringComparison]::OrdinalIgnoreCase) -or
            $candidate.EndsWith("/$expected", [System.StringComparison]::OrdinalIgnoreCase)) {
          return $true
        }
      }
    }
    $commandPosition = $false
  }
  return $false
}

function Test-IsGuardCommand {
  param([object]$Hook)
  if (-not (Test-IsJsonObject $Hook)) {
    return $false
  }
  $type = [string](Get-ObjectPropertyValue -Object $Hook -Name "type")
  $command = [string](Get-ObjectPropertyValue -Object $Hook -Name "command")
  return $type -eq "command" -and
    (Test-IsFrameworkPythonCommand -Command $command -ScriptName "guard-agent-tool.py")
}

function Test-IsGuardEntry {
  param([object]$Entry)
  if (-not (Test-IsJsonObject $Entry)) {
    return $false
  }
  $hooks = Get-ObjectPropertyValue -Object $Entry -Name "hooks"
  if (-not ($hooks -is [System.Array])) {
    return $false
  }
  foreach ($hook in $hooks) {
    if (Test-IsGuardCommand $hook) {
      return $true
    }
  }
  return $false
}

function Test-IsProbeRecorderCommand {
  param([object]$Hook)
  if (-not (Test-IsJsonObject $Hook)) {
    return $false
  }
  $type = [string](Get-ObjectPropertyValue -Object $Hook -Name "type")
  $command = [string](Get-ObjectPropertyValue -Object $Hook -Name "command")
  return $type -eq "command" -and
    (Test-IsFrameworkPythonCommand -Command $command -ScriptName "record-page-probe.py")
}

function Test-IsProbeRecorderEntry {
  param([object]$Entry)
  if (-not (Test-IsJsonObject $Entry)) {
    return $false
  }
  $hooks = Get-ObjectPropertyValue -Object $Entry -Name "hooks"
  if (-not ($hooks -is [System.Array])) {
    return $false
  }
  foreach ($hook in $hooks) {
    if (Test-IsProbeRecorderCommand $hook) {
      return $true
    }
  }
  return $false
}

function Add-EntryWithoutGuardCommand {
  param(
    [object]$Entry,
    [System.Collections.Generic.List[object]]$Destination,
    [System.Collections.Generic.HashSet[string]]$Seen,
    [string]$Label
  )
  if (-not (Test-IsJsonObject $Entry)) {
    throw "$Label hook entries must be JSON objects."
  }

  $entryHooks = Get-ObjectPropertyValue -Object $Entry -Name "hooks"
  if (-not ($entryHooks -is [System.Array])) {
    throw "$Label hook entry must contain a hooks array."
  }
  $candidate = $Entry
  $remainingHooks = @(
    $entryHooks | Where-Object {
      -not (Test-IsGuardCommand $_) -and -not (Test-IsProbeRecorderCommand $_)
    }
  )
  if ($remainingHooks.Count -ne $entryHooks.Count) {
    if ($remainingHooks.Count -eq 0) {
      return
    }
    $candidate = ($Entry | ConvertTo-Json -Depth 100 -Compress | ConvertFrom-Json)
    $candidate.hooks = @($remainingHooks)
  }

  $key = $candidate | ConvertTo-Json -Depth 100 -Compress
  if ($Seen.Add($key)) {
    $Destination.Add($candidate)
  }
}

function Merge-CodeBuddySettings {
  param(
    [string]$CurrentPath,
    [string]$IncomingPath
  )

  $incoming = Read-JsonObjectFile -Path $IncomingPath -Label "Incoming .codebuddy/settings.json"
  if (Test-Path -LiteralPath $CurrentPath) {
    $current = Read-JsonObjectFile -Path $CurrentPath -Label "Existing .codebuddy/settings.json"
  }
  else {
    $current = [PSCustomObject]@{}
  }

  $incomingHooks = Get-ObjectPropertyValue -Object $incoming -Name "hooks"
  if (-not (Test-IsJsonObject $incomingHooks)) {
    throw "Incoming .codebuddy/settings.json must contain a hooks object."
  }
  $incomingPreToolUse = @(Get-HookEventEntries -Hooks $incomingHooks -EventName "PreToolUse" -Label "Incoming .codebuddy/settings.json")
  $incomingGuardEntries = @($incomingPreToolUse | Where-Object { Test-IsGuardEntry $_ })
  if ($incomingGuardEntries.Count -ne 1) {
    throw "Incoming .codebuddy/settings.json must contain exactly one test-design guard hook."
  }
  $canonicalGuard = $incomingGuardEntries[0]
  $canonicalGuardCommands = @(
    (Get-ObjectPropertyValue -Object $canonicalGuard -Name "hooks") |
      Where-Object { Test-IsGuardCommand $_ }
  )
  if ($canonicalGuardCommands.Count -ne 1) {
    throw "Incoming .codebuddy/settings.json guard entry must contain exactly one guard command."
  }
  $incomingPostToolUse = @(Get-HookEventEntries -Hooks $incomingHooks -EventName "PostToolUse" -Label "Incoming .codebuddy/settings.json")
  $incomingRecorderEntries = @($incomingPostToolUse | Where-Object { Test-IsProbeRecorderEntry $_ })
  if ($incomingRecorderEntries.Count -ne 1) {
    throw "Incoming .codebuddy/settings.json must contain exactly one page probe recorder hook."
  }
  $canonicalRecorder = $incomingRecorderEntries[0]
  $canonicalRecorderCommands = @(
    (Get-ObjectPropertyValue -Object $canonicalRecorder -Name "hooks") |
      Where-Object { Test-IsProbeRecorderCommand $_ }
  )
  if ($canonicalRecorderCommands.Count -ne 1) {
    throw "Incoming .codebuddy/settings.json recorder entry must contain exactly one recorder command."
  }

  $currentHooksProperty = $current.PSObject.Properties["hooks"]
  if ($null -eq $currentHooksProperty) {
    $currentHooks = [PSCustomObject]@{}
  }
  else {
    $currentHooks = $currentHooksProperty.Value
  }
  if (-not (Test-IsJsonObject $currentHooks)) {
    throw "Existing .codebuddy/settings.json hooks must be a JSON object."
  }

  $mergedTopLevel = [ordered]@{}
  foreach ($property in $current.PSObject.Properties) {
    if ($property.Name -ne "hooks") {
      $mergedTopLevel[$property.Name] = $property.Value
    }
  }
  foreach ($property in $incoming.PSObject.Properties) {
    if ($property.Name -ne "hooks" -and -not $mergedTopLevel.Contains($property.Name)) {
      $mergedTopLevel[$property.Name] = $property.Value
    }
  }

  $eventNames = New-Object System.Collections.Generic.List[string]
  foreach ($hooksObject in @($currentHooks, $incomingHooks)) {
    foreach ($property in $hooksObject.PSObject.Properties) {
      if (-not $eventNames.Contains($property.Name)) {
        $eventNames.Add($property.Name)
      }
    }
  }

  $mergedHooks = [ordered]@{}
  foreach ($eventName in $eventNames) {
    $entries = New-Object System.Collections.Generic.List[object]
    $seen = New-Object System.Collections.Generic.HashSet[string]
    foreach ($entry in @(Get-HookEventEntries -Hooks $currentHooks -EventName $eventName -Label "Existing .codebuddy/settings.json")) {
      Add-EntryWithoutGuardCommand -Entry $entry -Destination $entries -Seen $seen -Label "Existing .codebuddy/settings.json"
    }
    foreach ($entry in @(Get-HookEventEntries -Hooks $incomingHooks -EventName $eventName -Label "Incoming .codebuddy/settings.json")) {
      if ($eventName -eq "PreToolUse" -and (Test-IsGuardEntry $entry)) {
        continue
      }
      if ($eventName -eq "PostToolUse" -and (Test-IsProbeRecorderEntry $entry)) {
        continue
      }
      Add-EntryWithoutGuardCommand -Entry $entry -Destination $entries -Seen $seen -Label "Incoming .codebuddy/settings.json"
    }
    if ($eventName -eq "PreToolUse") {
      $entries.Add($canonicalGuard)
    }
    if ($eventName -eq "PostToolUse") {
      $entries.Add($canonicalRecorder)
    }
    $mergedHooks[$eventName] = $entries.ToArray()
  }

  $mergedTopLevel["hooks"] = [PSCustomObject]$mergedHooks
  $json = [PSCustomObject]$mergedTopLevel | ConvertTo-Json -Depth 100
  [System.IO.File]::WriteAllText($IncomingPath, $json + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
}

function Read-RemovalManifest {
  param([string]$Path)
  $document = Read-JsonObjectFile -Path $Path -Label $removalManifestName
  $propertyNames = @($document.PSObject.Properties.Name | Sort-Object)
  if (($propertyNames -join ",") -cne "remove_files,schema_version") {
    throw "$removalManifestName contains unsupported properties."
  }
  if ([string]$document.schema_version -ne "1.0.0") {
    throw "$removalManifestName has an unsupported schema_version."
  }
  if (-not ($document.remove_files -is [System.Array])) {
    throw "$removalManifestName remove_files must be a JSON array."
  }
  $result = New-Object System.Collections.Generic.List[string]
  foreach ($relative in $document.remove_files) {
    if (-not ($relative -is [string]) -or -not $relative) {
      throw "$removalManifestName contains a non-string or empty removal path."
    }
    $normalized = Normalize-RelativePath $relative
    if ($relative -cne $normalized -or $normalized.Contains("..") -or $normalized.Contains(":")) {
      throw "$removalManifestName contains an unsafe removal path: $relative"
    }
    if ($supportedLegacyRemovalPaths -cnotcontains $normalized) {
      throw "$removalManifestName requests an unsupported removal path: $normalized"
    }
    if ($result.Contains($normalized)) {
      throw "$removalManifestName contains a duplicate removal path: $normalized"
    }
    $result.Add($normalized)
  }
  if ($result.Count -ne $supportedLegacyRemovalPaths.Count) {
    throw "$removalManifestName must declare every supported legacy removal exactly once."
  }
  foreach ($expected in $supportedLegacyRemovalPaths) {
    if (-not $result.Contains($expected)) {
      throw "$removalManifestName is missing required legacy removal: $expected"
    }
  }
  return @($result)
}

function Restore-UpgradeSnapshot {
  param(
    [string]$RepositoryRoot,
    [string]$SnapshotRoot,
    [System.Collections.Generic.List[string]]$CreatedTargets,
    [hashtable]$ProtectedPathStates
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
    $target = Join-Path $RepositoryRoot $protected
    $wasPresent = $ProtectedPathStates.ContainsKey($protected) -and [bool]$ProtectedPathStates[$protected]
    if (-not $wasPresent) {
      if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Recurse -Force
      }
      continue
    }
    if (-not (Test-Path -LiteralPath $snapshot)) {
      throw "Protected asset snapshot is missing during rollback: $protected"
    }
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
  $removalManifest = Join-Path $extractRoot $removalManifestName
  $removalPaths = @(Read-RemovalManifest -Path $removalManifest)
  foreach ($required in $requiredFrameworkFiles) {
    $requiredPath = Join-Path $extractRoot $required
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
      throw "Upgrade package is missing required framework file: $required"
    }
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
    if ($normalized -eq $removalManifestName) {
      return
    }
    if (Test-ProtectedPath $normalized) {
      Write-Host "Skip protected asset path: $normalized"
      return
    }
    if (-not (Test-AllowedPath $normalized)) {
      throw "Upgrade package contains an unexpected path: $normalized"
    }
    $packageFiles.Add([PSCustomObject]@{ Source = $_.FullName; Relative = $relative })
  }

  New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
  $protectedPathStates = @{}
  foreach ($protected in $protectedPrefixes) {
    $source = Join-Path $repoRoot $protected
    $protectedPathStates[$protected] = Test-Path -LiteralPath $source
    if ($protectedPathStates[$protected]) {
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
  foreach ($relative in $removalPaths) {
    if ($packageFiles.Relative -contains $relative.Replace("/", "\")) {
      throw "Upgrade package both installs and removes the same path: $relative"
    }
    $target = Join-Path $repoRoot $relative
    if (-not (Test-Path -LiteralPath $target)) {
      continue
    }
    $targetItem = Get-Item -LiteralPath $target -Force
    if ($targetItem.PSIsContainer -or ($targetItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
      throw "Refusing to remove non-regular legacy path: $relative"
    }
    $snapshot = Join-Path $frameworkSnapshot $relative
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $snapshot) | Out-Null
    Copy-Item -LiteralPath $target -Destination $snapshot -Force
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
    Merge-CodeBuddySettings -CurrentPath (Join-Path $repoRoot ".codebuddy\settings.json") -IncomingPath (Join-Path $extractRoot ".codebuddy\settings.json")
    Write-Host "Merged .codebuddy/settings.json while preserving existing local configuration and non-guard hooks."
    foreach ($item in $packageFiles) {
      $target = Join-Path $repoRoot $item.Relative
      New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
      Copy-Item -LiteralPath $item.Source -Destination $target -Force
    }
    foreach ($relative in $removalPaths) {
      $target = Join-Path $repoRoot $relative
      if (Test-Path -LiteralPath $target) {
        $targetItem = Get-Item -LiteralPath $target -Force
        if ($targetItem.PSIsContainer -or ($targetItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
          throw "Refusing to remove non-regular legacy path: $relative"
        }
        Remove-Item -LiteralPath $target -Force
        Write-Host "Removed deprecated framework file: $relative"
      }
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
    Restore-UpgradeSnapshot -RepositoryRoot $repoRoot -SnapshotRoot $backupRoot -CreatedTargets $createdTargets -ProtectedPathStates $protectedPathStates
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
