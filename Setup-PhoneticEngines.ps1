#Requires -Version 5.1
<#
.SYNOPSIS
Устанавливает ZIPA и Wav2Vec2Phoneme в отдельные окружения и загружает закреплённые модели
.DESCRIPTION
Требует Python 3.12 и интернет, сохраняет компоненты вне репозитория
Не изменяет окружение Allosaurus и не обрабатывает пользовательские аудио
#>
[CmdletBinding()]
param(
    [ValidateSet("all", "zipa", "w2v2")][string]$Engine = "all",
    [string]$PythonPath,
    [string]$RuntimeDirectory
)

$ErrorActionPreference = "Stop"
if (-not $RuntimeDirectory) { $RuntimeDirectory = Join-Path $env:USERPROFILE "glossolalia-engines" }
$engines = @($Engine)
if ($Engine -eq "all") { $engines = @("zipa", "w2v2") }

$previousEncoding = [Console]::OutputEncoding
try {
    [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)

    foreach ($name in $engines) {
        $environment = Join-Path $RuntimeDirectory "$name\.venv"
        $python = Join-Path $environment "Scripts\python.exe"
        if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
            if ($PythonPath) { & $PythonPath -m venv $environment } else { & py -3.12 -m venv $environment }
            if ($LASTEXITCODE -ne 0) { throw "Не удалось создать Python 3.12 для $name" }
        }

        & $python -c "import sys; assert sys.version_info[:2] == (3, 12), 'Expected Python 3.12'"
        if ($LASTEXITCODE -ne 0) { throw "Требуется Python 3.12: $python" }

        $torchPackages = @("torch==2.8.0")
        if ($name -eq "zipa") { $torchPackages += "torchaudio==2.8.0" }
        & $python -m pip install --disable-pip-version-check --index-url https://download.pytorch.org/whl/cpu @torchPackages
        if ($LASTEXITCODE -ne 0) { throw "Не удалось установить PyTorch для $name" }

        & $python -m pip install --disable-pip-version-check -r (Join-Path $PSScriptRoot "requirements\$name.txt")
        if ($LASTEXITCODE -ne 0) { throw "Не удалось установить зависимости $name" }

        & $python -m pip check
        if ($LASTEXITCODE -ne 0) { throw "Несовместимые зависимости $name" }

        & $python -B -X utf8 (Join-Path $PSScriptRoot "scripts\install_phonetic_model.py") --engine $name --runtime $RuntimeDirectory
        if ($LASTEXITCODE -ne 0) { throw "Не удалось загрузить модель $name" }

        & (Join-Path $PSScriptRoot "Setup-Pauses.ps1") -RuntimeDirectory $RuntimeDirectory -PythonPath $python

        & $python -B -X utf8 (Join-Path $PSScriptRoot "src\phonetic_engine.py") --engine $name --runtime $RuntimeDirectory --check
        if ($LASTEXITCODE -ne 0) { throw "Модель $name установлена, но не прошла проверку загрузки" }
    }

    Write-Host "Установка завершена: $RuntimeDirectory"
} finally {
    [Console]::OutputEncoding = $previousEncoding
}
