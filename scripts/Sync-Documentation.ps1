#Requires -Version 5.1
<#
.SYNOPSIS
Проверяет реестр и Markdown, а с -Write обновляет карту документов
.DESCRIPTION
Читает только видимые Git файлы проекта без сети и исполнения команд из Markdown
Меняет только docs/dev/tag-map.md при -Write и не проверяет истинность текста
#>
[CmdletBinding()]
param(
    [string]$Root,
    [switch]$Write
)
$ErrorActionPreference = "Stop"
# В Windows PowerShell 5.1 корень скрипта вычисляется после привязки параметров
if (-not $Root) { $Root = Split-Path -Parent $PSScriptRoot }
$Root = [IO.Path]::GetFullPath($Root)
$registryPath = "docs/dev/documentation.json"
$mapPath = "docs/dev/tag-map.md"
$utf8 = New-Object Text.UTF8Encoding($false, $true)
$previousEncoding = [Console]::OutputEncoding

try {
    [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
    $gitFiles = @(& git -C $Root -c core.quotepath=false ls-files --cached --others --exclude-standard)
    if ($LASTEXITCODE -ne 0) { throw "Git inventory failed: $Root" }

    # Исключения применяются и к ранее отслеживаемым файлам, прежде чем читать их содержимое
    $excluded = @(& git -C $Root -c core.quotepath=false ls-files --cached --others --ignored --exclude-standard)
    if ($LASTEXITCODE -ne 0) { throw "Git exclusions failed" }

    if (Test-Path -LiteralPath (Join-Path $Root ".aiignore")) {
        $excluded += @(& git -C $Root -c core.quotepath=false ls-files --cached --others --ignored --exclude-from=.aiignore)
        if ($LASTEXITCODE -ne 0) { throw ".aiignore exclusions failed" }
    }
} finally {
    [Console]::OutputEncoding = $previousEncoding
}
$gitFiles = @($gitFiles | Where-Object { $_ -cnotin $excluded } | Sort-Object -Unique)
if ($registryPath -cnotin $gitFiles) { throw "Registry is missing or excluded: $registryPath" }
$registry = [IO.File]::ReadAllText((Join-Path $Root $registryPath), $utf8) | ConvertFrom-Json
if ($registry.version -ne 1) { throw "Unsupported documentation registry version" }

$documents = @($registry.documents)
$markdownFiles = @($gitFiles | Where-Object { $_.EndsWith(".md", [StringComparison]::OrdinalIgnoreCase) })
$paths = @()
$tagOwners = @{}
$contents = @{}
$anchors = @{}
$issues = New-Object 'System.Collections.Generic.List[string]'
$allowedStatuses = @("current", "plan", "evidence", "reference", "process", "template", "generated")

function Add-Issue {
    param([string]$Message)
    $issues.Add($Message)
}

function Get-MarkdownBody {
    param([string]$Text, [string]$Path)

    $body = New-Object 'System.Collections.Generic.List[string]'
    $fence = ""
    $fenceLength = 0
    $previousLevel = 0
    $headings = @()
    $pageAnchors = @()
    $slugCounts = @{}
    foreach ($line in ($Text -split "\r?\n")) {
        if ($line -match '^\s{0,3}(`{3,}|~{3,})(.*)$') {
            $marker = $Matches[1]
            if (-not $fence) {
                $fence = $marker.Substring(0, 1)
                $fenceLength = $marker.Length
            } elseif ($marker.StartsWith($fence) -and $marker.Length -ge $fenceLength -and -not $Matches[2].Trim()) {
                $fence = ""
            }
            continue
        }
        if ($fence) { continue }

        $body.Add($line)
        if ($line -match '^(#{1,6})\s+(.+?)\s*#*\s*$') {
            $level = $Matches[1].Length
            $heading = $Matches[2]
            if ($level -eq 1) { $headings += $heading }
            if ($level -gt $previousLevel + 1) { Add-Issue "$Path : heading level jumps to H$level" }
            $previousLevel = $level
            $slug = ($heading.ToLowerInvariant() -replace '[^\p{L}\p{M}\p{N}_\-\s]', '') -replace '\s', '-'
            if ($slugCounts.ContainsKey($slug)) {
                $slugCounts[$slug]++
                $pageAnchors += "$slug-$($slugCounts[$slug])"
            } else {
                $slugCounts[$slug] = 0
                $pageAnchors += $slug
            }
        }
        foreach ($match in [regex]::Matches($line, '<a\s+(?:id|name)=["'']([^"'']+)["'']\s*></a>')) {
            $pageAnchors += $match.Groups[1].Value
        }
    }
    if ($fence) { Add-Issue "$Path : unclosed code fence" }
    if ($headings.Count -ne 1) { Add-Issue "$Path : expected one H1, got $($headings.Count)" }
    $anchors[$Path] = $pageAnchors
    [pscustomobject]@{ Body=($body -join "`n"); Title=($headings | Select-Object -First 1) }
}

foreach ($document in $documents) {
    $path = [string]$document.path
    if (-not $path -or $path -match '(^/|\\|:|(^|/)\.\.(/|$))' -or -not $path.EndsWith(".md")) {
        Add-Issue "Invalid registry path: $path"
        continue
    }
    if ($path -in $paths) { Add-Issue "Duplicate registry path: $path"; continue }
    $paths += $path
    if ($document.status -cnotin $allowedStatuses) { Add-Issue "$path : unknown document status" }
    if (-not $document.title -or -not $document.purpose -or "$($document.title)$($document.purpose)" -match '[|\r\n]') {
        Add-Issue "$path : title and purpose must be nonempty single-line text without table separators"
    }
    foreach ($tag in @($document.tags)) {
        if ($tag -cnotmatch '^(?:[a-z][a-z0-9]{2,23}|T[0-9]{5})$') { Add-Issue "$path : invalid tag $tag"; continue }
        if ($tagOwners.ContainsKey($tag)) { Add-Issue "Duplicate tag: $tag" } else { $tagOwners[$tag] = $path }
    }
    if ($path -ceq $mapPath -and $Write) { continue }
    if ($path -cnotin $markdownFiles) { Add-Issue "$path : missing or excluded document"; continue }

    $text = [IO.File]::ReadAllText((Join-Path $Root $path), $utf8)
    $parsed = Get-MarkdownBody -Text $text -Path $path
    if ($parsed.Title -cne $document.title) { Add-Issue "$path : H1 differs from registry title" }
    $contents[$path] = $parsed.Body
}
foreach ($path in $markdownFiles) {
    if ($path -cnotin $paths) { Add-Issue "Unregistered Markdown: $path" }
}
if ($mapPath -cnotin $paths) { Add-Issue "Generated map must be registered" }

function Test-LocalLink {
    param([string]$Source, [string]$Target)

    if ($Target -match '^(https?://|mailto:)' ) { return }
    if ($Target -match '^[a-zA-Z][a-zA-Z0-9+.-]*:|^[/\\]') {
        Add-Issue "$Source : unsupported or absolute link $Target"
        return
    }
    $parts = $Target -split '#', 2
    $relativePath = [Uri]::UnescapeDataString(($parts[0] -split '\?', 2)[0])
    $fragment = if ($parts.Count -eq 2) { [Uri]::UnescapeDataString($parts[1]) } else { "" }
    if ($relativePath) {
        $sourceDirectory = Split-Path -Parent (Join-Path $Root $Source)
        $fullPath = [IO.Path]::GetFullPath((Join-Path $sourceDirectory $relativePath))
        $prefix = $Root.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
        if (-not $fullPath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
            Add-Issue "$Source : link escapes repository: $Target"
            return
        }
        $linkedPath = $fullPath.Substring($prefix.Length).Replace('\', '/')
    } else {
        $linkedPath = $Source
    }
    if ($linkedPath -cnotin $gitFiles -and -not ($Write -and $linkedPath -ceq $mapPath)) {
        Add-Issue "$Source : missing or excluded link $Target"
        return
    }
    if ($fragment -and $linkedPath.EndsWith(".md") -and $fragment -cnotin @($anchors[$linkedPath])) {
        Add-Issue "$Source : missing anchor $Target"
    }
}

foreach ($path in @($contents.Keys)) {
    $body = $contents[$path]
    foreach ($match in [regex]::Matches($body, '§([a-z][a-z0-9]*|T[0-9]{5})(?![A-Za-z0-9])')) {
        if (-not $tagOwners.ContainsKey($match.Groups[1].Value)) { Add-Issue "$path : unknown tag $($match.Value)" }
    }
    $definitions = @{}
    foreach ($match in [regex]::Matches($body, '(?m)^\s{0,3}\[([^\]]+)\]:\s*(?:<([^>]+)>|(\S+))')) {
        $target = if ($match.Groups[2].Success) { $match.Groups[2].Value } else { $match.Groups[3].Value }
        $definitions[$match.Groups[1].Value.ToLowerInvariant()] = $target
        Test-LocalLink -Source $path -Target $target
    }
    $inlinePattern = '(?<image>!)?\[(?<label>[^\]\r\n]*)\]\((?:<(?<path>[^>\r\n]+)>|(?<path>[^()\s]+(?:\([^()\r\n]*\)[^()\s]*)*))(?:\s+"[^"]*")?\)'
    foreach ($match in [regex]::Matches($body, $inlinePattern)) {
        if ($match.Groups["image"].Success -and -not $match.Groups["label"].Value.Trim()) { Add-Issue "$path : image has no alt text" }
        Test-LocalLink -Source $path -Target $match.Groups["path"].Value
    }
    foreach ($match in [regex]::Matches($body, '(?<image>!)?\[(?<label>[^\]\r\n]*)\]\[(?<id>[^\]\r\n]*)\]')) {
        $id = $match.Groups["id"].Value
        if (-not $id) { $id = $match.Groups["label"].Value }
        if (-not $definitions.ContainsKey($id.ToLowerInvariant())) { Add-Issue "$path : missing link definition $id" }
        if ($match.Groups["image"].Success -and -not $match.Groups["label"].Value.Trim()) { Add-Issue "$path : image has no alt text" }
    }
}

$statusNames = @{
    current="Актуальный документ"; plan="План"; evidence="Датированная проверка"
    reference="Справочный материал"; process="Правила сопровождения"; template="Шаблон"; generated="Создаётся автоматически"
}
$mapLines = @(
    "# Карта меток и каталог документов", "",
    "<!-- Generated by scripts/Sync-Documentation.ps1 from docs/dev/documentation.json -->", "",
    "Источник — [реестр](documentation.json). Обновление и проверка: [TESTING](../TESTING.md). Не редактируйте карту вручную.", "",
    "Метка обозначает владельца темы; статус относится к документу и не подтверждает научный вывод.", "",
    "| Метки | Документ | Статус | Назначение |",
    "| --- | --- | --- | --- |"
)
foreach ($document in $documents) {
    $tagText = (@($document.tags | ForEach-Object { "§$_" }) -join ", ")
    if (-not $tagText) { $tagText = "—" }
    $link = "../../" + [string]$document.path
    $mapLines += "| $tagText | [$($document.title)](<$link>) | $($statusNames[$document.status]) | $($document.purpose) |"
}
$expectedMap = ($mapLines -join "`n") + "`n"
if (-not $Write -and (Test-Path -LiteralPath (Join-Path $Root $mapPath))) {
    $actualMap = [IO.File]::ReadAllText((Join-Path $Root $mapPath), $utf8).Replace("`r`n", "`n")
    if ($actualMap -cne $expectedMap) { Add-Issue "Generated map is stale; run scripts/Sync-Documentation.ps1 -Write" }
}
if ($issues.Count -gt 0) { throw ("Documentation audit failed:`n" + ($issues -join "`n")) }
if ($Write) { [IO.File]::WriteAllText((Join-Path $Root $mapPath), $expectedMap, $utf8) }
Write-Output "Documentation OK: $($documents.Count) documents, $($tagOwners.Count) tags"
