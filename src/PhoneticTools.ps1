#Requires -Version 5.1
<#
.SYNOPSIS
Общий диалог выбора и запуск локальных ZIPA и Wav2Vec2Phoneme
#>
function Start-PhoneticEngine {
    [CmdletBinding()]
    param(
        [ValidateSet("all", "allosaurus", "zipa", "w2v2")][string]$Engine,
        [string]$ProjectDirectory,
        [string[]]$AudioPath,
        [string]$Profile,
        [string]$InitialDirectory,
        [string]$RuntimeDirectory,
        [string]$PythonPath,
        [string]$AllosaurusPythonPath,
        [string]$FfmpegPath = "ffmpeg",
        [switch]$CheckOnly
    )

    $ErrorActionPreference = "Stop"
    if (-not $RuntimeDirectory) { $RuntimeDirectory = Join-Path $env:USERPROFILE "glossolalia-engines" }
    $engines = @($Engine)
    if ($Engine -eq "all") { $engines = @("allosaurus", "zipa", "w2v2") }
    if (-not $AllosaurusPythonPath) {
        $AllosaurusPythonPath = Join-Path $ProjectDirectory ".venv\Scripts\python.exe"
        if (-not (Test-Path -LiteralPath $AllosaurusPythonPath -PathType Leaf)) {
            $AllosaurusPythonPath = Join-Path $env:USERPROFILE "allosaurus-env\Scripts\python.exe"
        }
    }

    $workerArguments = @("-B", "-X", "utf8", (Join-Path $ProjectDirectory "src\phonetic_engine.py"), "--runtime", $RuntimeDirectory)
    if ($Profile) { $workerArguments += @("--profile", $Profile) }
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
                $picker.Title = "Выберите записи для фонетического распознавания с паузами"
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
        $failedEngines = 0
        foreach ($name in $engines) {
            $python = $PythonPath
            if (-not $python) {
                $environment = $name
                if ($name -eq "allosaurus") { $environment = "w2v2" }
                $python = Join-Path $RuntimeDirectory "$environment\.venv\Scripts\python.exe"
            }
            if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
                Write-Host "Python для $name не найден: выполните Setup-PhoneticEngines.ps1" -ForegroundColor Red
                $failedEngines++
                continue
            }

            $arguments = $workerArguments + @("--engine", $name, "--allosaurus-python", $AllosaurusPythonPath)
            $engineFailed = $false
            try {
                & $python @arguments | ForEach-Object {
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
                $engineFailed = $LASTEXITCODE -ne 0
            } catch {
                Write-Host $_.Exception.Message -ForegroundColor Red
                $engineFailed = $true
            }
            if ($engineFailed) {
                Write-Host "Движок $name завершился с ошибками; переход к следующему" -ForegroundColor Red
                $failedEngines++
            }
        }

        if ($failedEngines) { throw "Движков с ошибками: $failedEngines; подробности выше" }
    } finally {
        [Console]::OutputEncoding = $previousEncoding
        if ($manifest -and (Test-Path -LiteralPath $manifest)) { Remove-Item -LiteralPath $manifest -Force }
    }
}
