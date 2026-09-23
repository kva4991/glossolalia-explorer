#Requires -Version 5.1
<#
.SYNOPSIS
Добавляет общую локальную модель пауз к установленным движкам
#>
[CmdletBinding()]
param([string]$RuntimeDirectory, [string]$PythonPath)

$ErrorActionPreference = "Stop"
if (-not $RuntimeDirectory) { $RuntimeDirectory = Join-Path $env:USERPROFILE "glossolalia-engines" }
if (-not $PythonPath) { $PythonPath = Join-Path $RuntimeDirectory "w2v2\.venv\Scripts\python.exe" }
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) { throw "Сначала выполните Setup-PhoneticEngines.ps1 или укажите Python установленного движка" }

& $PythonPath -B -X utf8 (Join-Path $PSScriptRoot "scripts\install_pause_model.py") --runtime $RuntimeDirectory
if ($LASTEXITCODE -ne 0) { throw "Установка модели пауз завершилась с ошибкой" }
