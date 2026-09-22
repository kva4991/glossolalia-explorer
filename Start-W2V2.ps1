#Requires -Version 5.1
<#
.SYNOPSIS
Выбирает несколько записей и сохраняет отдельный TXT модели W2V2
#>
[CmdletBinding()]
param(
    [string[]]$AudioPath,
    [string]$InitialDirectory,
    [string]$RuntimeDirectory,
    [string]$PythonPath,
    [string]$FfmpegPath = "ffmpeg",
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
if (-not $InitialDirectory) { $InitialDirectory = Split-Path -Parent $PSScriptRoot }

. (Join-Path $PSScriptRoot "src\PhoneticTools.ps1")

$options = @{} + $PSBoundParameters
$options.InitialDirectory = $InitialDirectory
$options.FfmpegPath = $FfmpegPath

Start-PhoneticEngine -Engine "w2v2" -ProjectDirectory $PSScriptRoot @options
