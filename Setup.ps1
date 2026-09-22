#Requires -Version 5.1
[CmdletBinding()]
param([string]$PythonPath)

$ErrorActionPreference = "Stop"
$venvDirectory = Join-Path $PSScriptRoot ".venv"
$venvPython = Join-Path $venvDirectory "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    if ($PythonPath) {
        & $PythonPath -m venv $venvDirectory
    } else {
        & py -3.12 -m venv $venvDirectory
    }
    if ($LASTEXITCODE -ne 0) { throw "Не удалось создать окружение Python 3.12" }
}

& $venvPython -m pip install -r (Join-Path $PSScriptRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Не удалось установить зависимости" }

& $venvPython -B -X utf8 -m allosaurus.bin.download_model --model uni2005
if ($LASTEXITCODE -ne 0) { throw "Не удалось загрузить модель uni2005" }

Write-Host "Окружение готово: $venvPython"
Write-Host "FFmpeg должен быть доступен в PATH; проверка: .\Start-Glossolalia.ps1 -CheckOnly"
