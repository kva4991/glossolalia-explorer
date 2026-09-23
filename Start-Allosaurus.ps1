#Requires -Version 5.1
<#
.SYNOPSIS
Создаёт универсальную транскрипцию Allosaurus с паузами по аудио
#>
[CmdletBinding()]
param(
    [string[]]$AudioPath,
    [string]$Profile,
    [string]$InitialDirectory,
    [string]$RuntimeDirectory,
    [string]$AllosaurusPythonPath,
    [string]$FfmpegPath = "ffmpeg",
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
if (-not $InitialDirectory) { $InitialDirectory = Split-Path -Parent $PSScriptRoot }
. (Join-Path $PSScriptRoot "src\PhoneticTools.ps1")

$options = @{} + $PSBoundParameters
$options.InitialDirectory = $InitialDirectory
$options.FfmpegPath = $FfmpegPath
Start-PhoneticEngine -Engine "allosaurus" -ProjectDirectory $PSScriptRoot @options
