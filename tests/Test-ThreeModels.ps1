#Requires -Version 5.1
<#
.SYNOPSIS
Проверяет продолжение общего запуска после ошибки одного движка без моделей
#>
param([string]$PythonPath)

$ErrorActionPreference = "Stop"
if (-not $PythonPath) { $PythonPath = (Get-Command python -CommandType Application -ErrorAction Stop).Source }
$project = Split-Path -Parent $PSScriptRoot
. (Join-Path $project "src\PhoneticTools.ps1")

$testRoot = Join-Path ([IO.Path]::GetTempPath()) ("glossolalia-three-test-" + [guid]::NewGuid().ToString("N"))
[void](New-Item -ItemType Directory -Path (Join-Path $testRoot "src") -Force)
$audio = Join-Path $testRoot "sample.wav"
[IO.File]::WriteAllBytes($audio, [byte[]](1, 2, 3))
$worker = @'
import argparse, json, sys
from pathlib import Path
parser = argparse.ArgumentParser()
parser.add_argument('--engine')
parser.add_argument('--manifest')
parser.add_argument('--profile')
args, _ = parser.parse_known_args()
root = Path(__file__).resolve().parents[1]
data = json.loads(Path(args.manifest).read_text(encoding='utf-8'))
with (root / 'calls.jsonl').open('a', encoding='utf-8') as stream:
    stream.write(json.dumps({'engine': args.engine, 'files': data, 'profile': args.profile}) + '\n')
if args.engine == 'allosaurus':
    print('Expected test failure', file=sys.stderr)
    sys.exit(1)
print('__GLOSSOLALIA_SAVED__')
'@
[IO.File]::WriteAllText((Join-Path $testRoot "src\phonetic_engine.py"), $worker, (New-Object Text.UTF8Encoding($false)))

$failed = $false
try {
    Start-PhoneticEngine -Engine all -ProjectDirectory $testRoot -PythonPath $PythonPath -AudioPath @($audio, $audio) -FfmpegPath $PythonPath -Profile author-semitic
} catch {
    $failed = $true
}
if (-not $failed) { throw "Общая ошибка не была возвращена" }
$calls = @(Get-Content -LiteralPath (Join-Path $testRoot "calls.jsonl") | ForEach-Object { $_ | ConvertFrom-Json })
if (($calls.engine -join ',') -ne 'allosaurus,zipa,w2v2') { throw "После ошибки обработаны не все три модели" }
foreach ($call in $calls) {
    if ($call.profile -ne 'author-semitic') { throw "Профиль не передан одному из движков" }
    if (@($call.files).Count -ne 1 -or $call.files[0] -ne $audio) { throw "Повторный путь не устранён" }
}
if (([IO.File]::ReadAllBytes($audio) -join ',') -ne '1,2,3') { throw "Исходник изменён" }
Write-Output "Three-model tests OK; temporary data: $testRoot"
