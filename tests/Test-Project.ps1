#Requires -Version 5.1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $root "src\ProfileTools.ps1")

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

foreach ($relative in @("Start-Glossolalia.ps1", "Setup.ps1", "src\ProfileTools.ps1", "tests\Test-Project.ps1", "tests\Smoke-Allosaurus.ps1")) {
    $tokens = $null
    $errors = $null
    $null = [Management.Automation.Language.Parser]::ParseFile((Join-Path $root $relative), [ref]$tokens, [ref]$errors)
    Assert-True ($errors.Count -eq 0) "Syntax error: $relative"
}

$config = Get-Content -LiteralPath (Join-Path $root "languages.json") -Raw -Encoding UTF8 | ConvertFrom-Json
Assert-True ($config.Count -eq 16) "Expected 16 modes"
Assert-True (@($config | Where-Object { $_.profile }).Count -eq 5) "Expected 5 custom profiles"
Assert-True (@($config.code | Select-Object -Unique).Count -eq $config.Count) "Duplicate mode code"
$script:supported = @()
foreach ($lang in $config | Where-Object { $_.profile }) {
    $script:supported += [IO.File]::ReadAllLines((Join-Path $root $lang.profile), [Text.Encoding]::UTF8)
}
$script:supported = @($script:supported | Where-Object { $_ -notin @("tˠ", "sˠ", "ɬ") } | Select-Object -Unique)
$script:supported += "I"
$info = Get-ProfileInfo -Path (Join-Path $root "mandaic.txt") -SupportedPhones $script:supported
Assert-True (($info.Unsupported -join ",") -ceq "tˠ,sˠ") "Mandaic unsupported symbols lost"
Assert-True ($info.Supported.Count -eq 29) "Mandaic supported count incorrect"
$info = Get-ProfileInfo -Path (Join-Path $root "syr.txt") -SupportedPhones $script:supported
Assert-True (($info.Unsupported -join ",") -ceq "ɬ") "Syriac unsupported symbol lost"

$fixtureRoot = Join-Path ([IO.Path]::GetTempPath()) ("glossolalia-tests-" + [guid]::NewGuid().ToString("N"))
$null = [IO.Directory]::CreateDirectory($fixtureRoot)
$utf8 = New-Object Text.UTF8Encoding($false)
$newline = [Environment]::NewLine
foreach ($case in @(
    @{name="empty"; text=""},
    @{name="blank"; text=("a" + $newline + $newline + "b")},
    @{name="duplicate"; text=("a" + $newline + "a")},
    @{name="spaces"; text="a b"},
    @{name="unknown"; text="not-a-phone"}
)) {
    $path = Join-Path $fixtureRoot ($case.name + ".txt")
    [IO.File]::WriteAllText($path, $case.text, $utf8)
    $rejected = $false
    try { $null = Get-ProfileInfo -Path $path -SupportedPhones @("a", "b") } catch { $rejected = $true }
    Assert-True $rejected ("Invalid profile accepted: " + $case.name)
}
$bomPath = Join-Path $fixtureRoot "bom.txt"
[IO.File]::WriteAllText($bomPath, "a", (New-Object Text.UTF8Encoding($true)))
$rejected = $false
try { $null = Get-ProfileInfo -Path $bomPath -SupportedPhones @("a") } catch { $rejected = $true }
Assert-True $rejected "BOM accepted"
$casePath = Join-Path $fixtureRoot "case.txt"
[IO.File]::WriteAllText($casePath, ("I" + $newline + "i"), $utf8)
$info = Get-ProfileInfo -Path $casePath -SupportedPhones @("i")
Assert-True (($info.Unsupported -join ",") -ceq "I") "Phone matching must be case sensitive"

$script:conversionOutputs = @()
$script:conversions = @()
$script:recognitions = @()
$script:temporaryFiles = @()
$script:listCalls = 0
$script:listMode = "Success"
function New-FixtureTempFile {
    $path = [IO.Path]::GetTempFileName()
    $script:temporaryFiles += $path
    $path
}
function Invoke-FixtureFfmpeg {
    $inputPath = $args[[Array]::IndexOf($args, "-i") + 1]
    $outputPath = $args[-1]
    $script:conversions += $inputPath
    $script:conversionOutputs += $outputPath
    if ([IO.Path]::GetFileNameWithoutExtension($inputPath) -eq "failed") {
        [IO.File]::WriteAllText($outputPath, "partial-wav")
        $global:LASTEXITCODE = 2
        return
    }
    Assert-True (-not (Test-Path -LiteralPath $outputPath)) "Existing WAV passed to conversion"
    [IO.File]::WriteAllText($outputPath, "converted")
    $global:LASTEXITCODE = 0
}
function Invoke-FixtureAllosaurus {
    if ($args -contains "allosaurus.bin.list_lang") {
        $script:listCalls++
        $global:LASTEXITCODE = 0
        if ($script:listMode -eq "Fail") { $global:LASTEXITCODE = 4; return }
        if ($script:listMode -eq "Malformed") { "unexpected output"; return }
        foreach ($code in @("heb","arb","tam","hin","cmn","yor","eng","spa","amh","ckt")) {
            "- ISO639-3:  $code Glotto Code test0000 name: b'test'"
        }
        return
    }
    if ($args -contains "allosaurus.bin.list_phone") {
        $global:LASTEXITCODE = 0
        $script:supported -join " "
        return
    }
    $code = $args[[Array]::IndexOf($args, "--lang") + 1]
    $outputPath = $args[[Array]::IndexOf($args, "--output") + 1]
    Assert-True (($args -contains "--timestamp=True") -and ($args -contains "--topk=3")) "Combined top-k request missing"
    Assert-True ($args[[Array]::IndexOf($args, "--model") + 1] -eq "uni2005") "Wrong model"
    if ([IO.Path]::IsPathRooted($code)) {
        Assert-True (Test-Path -LiteralPath $code -PathType Leaf) "Missing profile reached recognizer"
        $code = [IO.Path]::GetFileNameWithoutExtension($code)
    }
    $script:recognitions += $code
    $global:LASTEXITCODE = 0
    if ($code -eq "heb") { $global:LASTEXITCODE = 7; return }
    if ($code -eq "hin") { throw "Controlled mode failure" }
    $text = "0.210 0.045 a (0.577) e (0.128) o (0.103)" + [Environment]::NewLine + "0.600 0.045 l (0.254) n (0.196) r (0.018)"
    if ($code -eq "arb") { $text = "0.210 0.045 a" }
    if ($code -eq "cmn") { $text = "" }
    [IO.File]::WriteAllText($outputPath, $text, (New-Object Text.UTF8Encoding($false)))
}

$audioPaths = @()
foreach ($name in @("existing.m4a","failed.m4a","fresh.mp3","direct.wav")) {
    $path = Join-Path $fixtureRoot $name
    [IO.File]::WriteAllText($path, "original")
    $audioPaths += $path
}
[IO.File]::WriteAllText((Join-Path $fixtureRoot "existing.wav"), "keep-wav")
[IO.File]::WriteAllText((Join-Path $fixtureRoot "existing.txt"), "keep-txt")
[IO.File]::WriteAllText((Join-Path $fixtureRoot "existing_NEW.txt"), "keep-new-txt")

$source = [IO.File]::ReadAllText((Join-Path $root "Start-Glossolalia.ps1"), [Text.Encoding]::UTF8)
$source = $source.Replace('$PSScriptRoot', ("'" + $root.Replace("'", "''") + "'"))
$source = $source.Replace('$pythonExe = $PythonPath', '$pythonExe = "Invoke-FixtureAllosaurus"')
$source = $source.Replace('$ffmpegExe = (Get-Command $FfmpegPath -CommandType Application -ErrorAction Stop).Source', '$ffmpegExe = "Invoke-FixtureFfmpeg"')
$source = $source.Replace('[System.IO.Path]::GetTempFileName()', '(New-FixtureTempFile)')
$runner = [scriptblock]::Create($source)
$messages = @(& $runner -AudioPath $audioPaths -PythonPath $PSCommandPath 6>&1)
Assert-True ($script:listCalls -eq 1) "Language list repeated per recording"
Assert-True ($script:recognitions.Count -eq 48) "Expected 16 attempts for each of 3 recordings"
Assert-True ($script:conversions.Count -eq 2) "WAV reuse failed"
Assert-True (@($messages | Where-Object { $_.ToString() -eq "        ██" }).Count -eq 3) "Missing checkmarks"

foreach ($name in @("existing_NEW_NEW.txt", "fresh.txt", "direct.txt")) {
    $report = [IO.File]::ReadAllText((Join-Path $fixtureRoot $name), [Text.Encoding]::UTF8)
    $codes = @([regex]::Matches($report, '(?m)^ЯЗЫК: .+ \(([^)]+)\)') | ForEach-Object { $_.Groups[1].Value })
    Assert-True (($codes -join ",") -eq ($config.code -join ",")) "Report modes differ"
    Assert-True ($report.Contains("Режимов обработано: 14 из 16")) "Failed modes counted as successful"
    Assert-True ([regex]::Matches($report, "ЭКСПЕРИМЕНТАЛЬНЫЙ, НЕОФИЦИАЛЬНЫЙ ПРОФИЛЬ").Count -eq 5) "Unofficial labels missing"
    Assert-True ($report.Contains("не поддерживает и не распознаёт: tˠ, sˠ")) "Mandaic limitation missing"
    Assert-True ($report.Contains("не поддерживает и не распознаёт: ɬ")) "Syriac limitation missing"
    Assert-True ($report.Contains("a (p=0.577)")) "Wrong top-k probability"
    Assert-True ($report.Contains("alt: e (0.128), o (0.103)")) "Wrong alternatives"
    Assert-True ($report.Contains("l (p=0.254) !!!")) "Confidence marker missing"
    Assert-True ($report.Contains("a (p=н/д)")) "Missing probability misreported"
    Assert-True ($report.Contains("Controlled mode failure")) "Exception not reported"
    Assert-True ($report.Contains("Фонемы не обнаружены")) "Empty output not reported"
}
Assert-True ([IO.File]::ReadAllText((Join-Path $fixtureRoot "existing.wav")) -eq "keep-wav") "Existing WAV changed"
Assert-True ([IO.File]::ReadAllText((Join-Path $fixtureRoot "direct.wav")) -eq "original") "Source WAV changed"
Assert-True ([IO.File]::ReadAllText((Join-Path $fixtureRoot "existing.txt")) -eq "keep-txt") "Existing TXT changed"
Assert-True ([IO.File]::ReadAllText((Join-Path $fixtureRoot "existing_NEW.txt")) -eq "keep-new-txt") "Existing _NEW TXT changed"
Assert-True (-not (Test-Path -LiteralPath (Join-Path $fixtureRoot "failed.txt"))) "Failed conversion created report"
foreach ($path in $script:temporaryFiles) { Assert-True (-not (Test-Path -LiteralPath $path)) "Temporary recognizer file not cleaned" }

foreach ($path in $script:conversionOutputs) { Assert-True (-not (Test-Path -LiteralPath $path)) "Partial conversion file not cleaned" }
Assert-True (-not (Test-Path -LiteralPath (Join-Path $fixtureRoot "failed.wav"))) "Partial WAV reused after failure"

$previousCalls = $script:recognitions.Count
$null = & $runner -PythonPath $PSCommandPath -CheckOnly -Modes ipa,mandaic
Assert-True ($script:recognitions.Count -eq $previousCalls) "CheckOnly recognized audio"
$rejected = $false
try { & $runner -PythonPath $PSCommandPath -CheckOnly -Modes unknown } catch { $rejected = $true }
Assert-True $rejected "Unknown mode accepted"
foreach ($mode in @("Fail", "Malformed")) {
    $script:listMode = $mode
    $rejected = $false
    try { & $runner -PythonPath $PSCommandPath -CheckOnly } catch { $rejected = $true }
    Assert-True $rejected "Invalid language list accepted"
}
Assert-True ($script:recognitions.Count -eq $previousCalls) "Recognition started after failed preflight"
[pscustomobject]@{ PowerShell=$PSVersionTable.PSVersion.ToString(); Reports=3; Modes=16; CustomProfiles=5; RecognitionCalls=48; Passed=$true } | ConvertTo-Json -Compress
