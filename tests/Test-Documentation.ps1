#Requires -Version 5.1
<#
.SYNOPSIS
Проверяет аудит документации на корректных и повреждённых мини-репозиториях
.DESCRIPTION
Исполняет настоящий аудитор с Git, включая кириллицу и исключённые личные файлы
Создаёт и удаляет только свою временную папку и не подтверждает факты документации
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$checker = Join-Path $root "scripts\Sync-Documentation.ps1"
$tempParent = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\', '/')
$fixtureRoot = Join-Path $tempParent ("glossolalia-doc-tests-" + [guid]::NewGuid().ToString("N"))
$utf8 = New-Object Text.UTF8Encoding($false)
$count = 0

function Write-Fixture {
    param([string]$Path, [string]$Text)
    $fullPath = Join-Path $fixtureRoot $Path
    $null = [IO.Directory]::CreateDirectory((Split-Path -Parent $fullPath))
    [IO.File]::WriteAllText($fullPath, $Text, $utf8)
}

function Assert-Audit {
    param([string]$ExpectedError = "", [switch]$Generate)
    $problem = ""
    try { $null = & $checker -Root $fixtureRoot -Write:$Generate } catch { $problem = $_.Exception.Message }
    if ($ExpectedError) {
        if (-not $problem.Contains($ExpectedError)) { throw "Expected '$ExpectedError', got '$problem'" }
    } elseif ($problem) {
        throw $problem
    }
    $script:count++
}

try {
    $null = [IO.Directory]::CreateDirectory($fixtureRoot)
    & git -C $fixtureRoot init --quiet
    if ($LASTEXITCODE -ne 0) { throw "Fixture git init failed" }

    Write-Fixture ".gitignore" "private/`n"
    Write-Fixture ".aiignore" "personal/`n"
    Write-Fixture "private/secret.md" "not Markdown"
    Write-Fixture "personal/secret.md" "not Markdown"
    Write-Fixture "docs/TESTING.md" "# Проверки`n"
    Write-Fixture "docs/T00001.md" "# §T00001 — Пример`n`nТекст §T00001`n"
    Write-Fixture "docs/Тема (тест).md" "# Тема`n`n## Раздел`n`nТекст §method`n"
    $readme = "# Fixture`n`n[Тема](<docs/Тема (тест).md#раздел>)`n`n[Повтор][topic]`n`n[topic]: <docs/Тема (тест).md>`n"
    Write-Fixture "README.md" $readme
    $registry = @{
        version=1
        documents=@(
            @{path="README.md"; title="Fixture"; status="current"; purpose="Entry"; tags=@("entry")},
            @{path="docs/TESTING.md"; title="Проверки"; status="process"; purpose="Checks"; tags=@("testing")},
            @{path="docs/T00001.md"; title="§T00001 — Пример"; status="reference"; purpose="Transcription"; tags=@("T00001")},
            @{path="docs/Тема (тест).md"; title="Тема"; status="current"; purpose="Topic"; tags=@("method")},
            @{path="docs/dev/tag-map.md"; title="Карта меток и каталог документов"; status="generated"; purpose="Map"; tags=@()}
        )
    }
    $registryText = $registry | ConvertTo-Json -Depth 6
    Write-Fixture "docs/dev/documentation.json" $registryText
    Assert-Audit -Generate
    Assert-Audit
    $mapPath = Join-Path $fixtureRoot "docs/dev/tag-map.md"
    $mapText = [IO.File]::ReadAllText($mapPath)

    $cases = @(
        @{text=($readme + "`n[Broken](missing.md)`n"); error="missing or excluded link"},
        @{text=$readme.Replace("#раздел", "#нет-раздела"); error="missing anchor"},
        @{text=($readme + "`n§unknown`n"); error="unknown tag"},
        @{text=($readme + "`n§T99999`n"); error="unknown tag"},
        @{text=($readme + "`n# Second`n"); error="expected one H1"},
        @{text=($readme + "`n### Jump`n"); error="heading level jumps"},
        @{text=($readme + "`n" + '```powershell' + "`nhello`n"); error="unclosed code fence"},
        @{text=($readme + "`n![](docs/TESTING.md)`n"); error="image has no alt text"},
        @{text=($readme + "`n[Unknown][missing]`n"); error="missing link definition"},
        @{text=($readme + "`n[Private](private/secret.md)`n"); error="missing or excluded link"},
        @{text=($readme + "`n[Private](personal/secret.md)`n"); error="missing or excluded link"},
        @{text=($readme + "`n[Outside](../outside.md)`n"); error="link escapes repository"}
    )
    foreach ($case in $cases) {
        Write-Fixture "README.md" $case.text
        Assert-Audit -ExpectedError $case.error
    }
    Assert-Audit -ExpectedError "link escapes repository" -Generate
    if ([IO.File]::ReadAllText($mapPath) -cne $mapText) { throw "Failed audit overwrote map" }
    Write-Fixture "README.md" $readme

    Write-Fixture "docs/dev/tag-map.md" ($mapText + "Manual change`n")
    Assert-Audit -ExpectedError "Generated map is stale"
    Assert-Audit -Generate
    Assert-Audit

    Write-Fixture "new.md" "# New`n"
    Assert-Audit -ExpectedError "Unregistered Markdown"
    Write-Fixture ".gitignore" "private/`nnew.md`n"
    Assert-Audit

    $registry.documents[3].tags = @("entry")
    Write-Fixture "docs/dev/documentation.json" ($registry | ConvertTo-Json -Depth 6)
    Assert-Audit -ExpectedError "Duplicate tag"
    Write-Fixture "docs/dev/documentation.json" $registryText
    Assert-Audit
    Write-Output "Documentation regression tests OK: $count cases"
} finally {
    # Удаляется только созданный этим запуском каталог внутри временной папки
    $resolved = [IO.Path]::GetFullPath($fixtureRoot)
    if ($resolved.StartsWith($tempParent + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -and
        [IO.Path]::GetFileName($resolved) -match '^glossolalia-doc-tests-[a-f0-9]{32}$') {
        if (Test-Path -LiteralPath $resolved) { Remove-Item -LiteralPath $resolved -Recurse -Force }
    } else {
        throw "Unsafe fixture cleanup path: $resolved"
    }
}
