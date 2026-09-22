#Requires -Version 5.1
[CmdletBinding()]
param([string]$PythonPath, [string]$FfmpegPath = "ffmpeg")

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$directory = Join-Path ([IO.Path]::GetTempPath()) ("glossolalia-smoke-" + [guid]::NewGuid().ToString("N"))
$null = [IO.Directory]::CreateDirectory($directory)
$audio = Join-Path $directory "synthetic.wav"
& $FfmpegPath -hide_banner -loglevel error -nostdin -n -f lavfi -i "sine=frequency=180:duration=1" -ac 1 -ar 16000 -c:a pcm_s16le $audio
if ($LASTEXITCODE -ne 0) { throw "Не удалось создать синтетический WAV" }

$parameters = @{ AudioPath=@($audio); Modes=@("ipa","galilean","mandaic","syr","grc","lat"); FfmpegPath=$FfmpegPath }
if ($PythonPath) { $parameters.PythonPath = $PythonPath }
& (Join-Path $root "Start-Glossolalia.ps1") @parameters

$report = [IO.File]::ReadAllText((Join-Path $directory "synthetic.txt"), [Text.Encoding]::UTF8)
if (-not $report.Contains("Режимов обработано: 6 из 6") -or -not $report.Contains("Недоступных режимов: 0")) {
    throw "Не все режимы завершились успешно: $directory"
}
Write-Host "Интеграционная проверка пройдена: $directory"
Write-Host "Проверено подключение профилей на синтетическом сигнале, не точность распознавания языков"
