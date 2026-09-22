#Requires -Version 5.1
<#
.SYNOPSIS
Общий диалог выбора и запуск локальных ZIPA и Wav2Vec2Phoneme
#>
function Start-PhoneticEngine {
    [CmdletBinding()]
    param(
        [ValidateSet("zipa", "w2v2")][string]$Engine,
        [string]$ProjectDirectory,
        [string[]]$AudioPath,
        [string]$InitialDirectory,
        [string]$RuntimeDirectory,
        [string]$PythonPath,
        [string]$FfmpegPath = "ffmpeg",
        [switch]$CheckOnly
    )

    $ErrorActionPreference = "Stop"
    if (-not $RuntimeDirectory) { $RuntimeDirectory = Join-Path $env:USERPROFILE "glossolalia-engines" }
    if (-not $PythonPath) { $PythonPath = Join-Path $RuntimeDirectory "$Engine\.venv\Scripts\python.exe" }
    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        throw "Python для $Engine не найден: выполните Setup-PhoneticEngines.ps1 -Engine $Engine"
    }

    $workerArguments = @("-B", "-X", "utf8", (Join-Path $ProjectDirectory "src\phonetic_engine.py"), "--engine", $Engine, "--runtime", $RuntimeDirectory)
    $manifest = $null
    $previousEncoding = [Console]::OutputEncoding

    try {
        if ($CheckOnly) {
            $workerArguments += "--check"
        } else {
            $paths = @()
            if ($AudioPath) {
                $paths = @($AudioPath | ForEach-Object {
                    $item = Get-Item -LiteralPath $_ -ErrorAction Stop
                    if ($item.PSIsContainer) { throw "Ожидался аудиофайл: $_" }
                    $item.FullName
                } | Select-Object -Unique)
            } else {
                Add-Type -AssemblyName System.Windows.Forms
                $picker = New-Object System.Windows.Forms.OpenFileDialog
                $picker.Title = "Выберите записи для $($Engine.ToUpperInvariant())"
                $picker.InitialDirectory = $InitialDirectory
                $picker.Filter = "Аудиофайлы|*.m4a;*.wav;*.mp3;*.flac;*.ogg;*.aac;*.wma;*.mp4|Все файлы|*.*"
                $picker.Multiselect = $true
                $picker.CheckFileExists = $true

                try {
                    if ($picker.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { return }
                    $paths = $picker.FileNames
                } finally {
                    $picker.Dispose()
                }
            }

            $ffmpeg = (Get-Command $FfmpegPath -CommandType Application -ErrorAction Stop).Source
            $manifest = [IO.Path]::GetTempFileName()
            $json = ConvertTo-Json -InputObject @($paths)
            [IO.File]::WriteAllText($manifest, $json, (New-Object Text.UTF8Encoding($false)))
            $workerArguments += @("--manifest", $manifest, "--ffmpeg", $ffmpeg)
        }

        # UTF-8 сохраняет кириллицу и IPA при чтении вывода Python в PowerShell 5.1
        [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
        & $PythonPath @workerArguments | ForEach-Object {
            if ($_ -eq "__GLOSSOLALIA_SAVED__") {
                Write-Host "        ██" -ForegroundColor Green
                Write-Host "       ██" -ForegroundColor Green
                Write-Host "  ██  ██" -ForegroundColor Green
                Write-Host "   ████" -ForegroundColor Green
                Write-Host "    ██" -ForegroundColor Green
            } else {
                Write-Host $_
            }
        }

        if ($LASTEXITCODE -ne 0) { throw "Обработка $Engine завершена с ошибками; подробности выше" }
    } finally {
        [Console]::OutputEncoding = $previousEncoding
        if ($manifest -and (Test-Path -LiteralPath $manifest)) { Remove-Item -LiteralPath $manifest -Force }
    }
}
