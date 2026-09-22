#Requires -Version 5.1
[CmdletBinding()]
param(
    [string[]]$AudioPath,
    [string]$InitialDirectory = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonPath,
    [string]$FfmpegPath = "ffmpeg",
    [string[]]$Modes,
    [string]$Model = "uni2005",
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "src\ProfileTools.ps1")

if (-not $PythonPath) {
    foreach ($candidate in @((Join-Path $PSScriptRoot ".venv\Scripts\python.exe"), (Join-Path $env:USERPROFILE "allosaurus-env\Scripts\python.exe"))) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $PythonPath = $candidate
            break
        }
    }
}

if (-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Python не найден: выполните Setup.ps1 или укажите -PythonPath"
}

$pythonExe = $PythonPath
$ffmpegExe = (Get-Command $FfmpegPath -CommandType Application -ErrorAction Stop).Source
$profileDirectory = $PSScriptRoot
$languages = Get-Content -LiteralPath (Join-Path $PSScriptRoot "languages.json") -Raw -Encoding UTF8 | ConvertFrom-Json
if ($Modes) {
    foreach ($mode in $Modes) {
        if ($mode -notin $languages.code) { throw "Неизвестный режим: $mode" }
    }
    $languages = @($languages | Where-Object { $_.code -in $Modes })
}

$audioPaths = @()
if (-not $CheckOnly) {
    if ($AudioPath) {
        $audioPaths = @($AudioPath | ForEach-Object {
            $item = Get-Item -LiteralPath $_ -ErrorAction Stop
            if ($item.PSIsContainer) { throw "Ожидался аудиофайл: $_" }
            $item.FullName
        } | Select-Object -Unique)
    } else {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
        $picker = New-Object System.Windows.Forms.OpenFileDialog
        $picker.Title = "Выберите исходные аудиозаписи"
        $picker.InitialDirectory = $InitialDirectory
        $picker.Filter = "Аудиофайлы|*.m4a;*.wav;*.mp3;*.flac;*.ogg;*.aac|Все файлы|*.*"
        $picker.CheckFileExists = $true
        $picker.Multiselect = $true
        try {
            if ($picker.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { return }
            $audioPaths = $picker.FileNames
        } finally {
            $picker.Dispose()
        }
    }
}

Write-Host "Проверка доступных языковых режимов..."
$languageList = @(& $pythonExe -B -X utf8 -m allosaurus.bin.list_lang --model $Model)
if ($LASTEXITCODE -ne 0) {
    throw "Не удалось получить список языков Allosaurus. Проверьте установку модели $Model"
}

# Универсальный режим поддерживается отдельно от списка языковых наборов
$availableLanguages = @{ ipa = $true }
foreach ($languageLine in $languageList) {
    $languageMatch = [regex]::Match($languageLine, 'ISO639-3:\s+(\S+)')
    if ($languageMatch.Success) {
        $availableLanguages[$languageMatch.Groups[1].Value] = $true
    }
}

if ($availableLanguages.Count -le 1) {
    throw "Список языков Allosaurus пуст или имеет неизвестный формат"
}

$phoneList = @(& $pythonExe -B -X utf8 -m allosaurus.bin.list_phone --model $Model --lang ipa)
if ($LASTEXITCODE -ne 0) { throw "Не удалось получить набор звуков модели $Model" }
$supportedPhones = @(($phoneList -join " ").Trim() -split '\s+')
if ($supportedPhones.Count -lt 2) { throw "Получен пустой набор звуков модели $Model" }

$profileInfo = @{}
$unavailableModes = @{}
foreach ($lang in $languages) {
    if ($lang.profile) {
        try {
            $info = Get-ProfileInfo -Path (Join-Path $profileDirectory $lang.profile) -SupportedPhones $supportedPhones
            $profileInfo[$lang.code] = $info
            if ($info.Unsupported.Count) {
                Write-Host "$($lang.name): модель $Model не поддерживает $($info.Unsupported -join ', ')" -ForegroundColor Yellow
            }
        } catch {
            $unavailableModes[$lang.code] = $_.Exception.Message
        }
    } elseif (-not $availableLanguages.ContainsKey($lang.code)) {
        $unavailableModes[$lang.code] = "Языковой режим отсутствует в модели $Model"
    }
}

if ($CheckOnly) {
    foreach ($lang in $languages) {
        if ($unavailableModes.ContainsKey($lang.code)) {
            Write-Host "$($lang.code): $($unavailableModes[$lang.code])" -ForegroundColor Yellow
        } elseif ($lang.profile) {
            $info = $profileInfo[$lang.code]
            Write-Host "$($lang.code): ЭКСПЕРИМЕНТАЛЬНЫЙ, НЕОФИЦИАЛЬНЫЙ ПРОФИЛЬ; доступно $($info.Supported.Count) из $($info.Phones.Count) звуков"
        } else {
            Write-Host "$($lang.code): доступен"
        }
    }
    if ($unavailableModes.Count) { throw "Недоступных режимов: $($unavailableModes.Count)" }
    Write-Host "Проверка завершена; модель $Model, режимов: $($languages.Count)"
    return
}

foreach ($audioPath in $audioPaths) {
    $basePath = Join-Path ([System.IO.Path]::GetDirectoryName($audioPath)) ([System.IO.Path]::GetFileNameWithoutExtension($audioPath))
    $wavPath = "$basePath.wav"
    $combinedPath = "$basePath.txt"

    while (Test-Path -LiteralPath $combinedPath) {
        $basePath += "_NEW"
        $combinedPath = "$basePath.txt"
    }

    Write-Host "Выбрана запись: $audioPath"
    if (Test-Path -LiteralPath $wavPath -PathType Leaf) {
        Write-Host "Используется готовый WAV: $wavPath"
    } else {
        Write-Host "Преобразование в WAV..."

        # Незавершённая конвертация не должна становиться готовым WAV при повторном запуске
        $tempWav = Join-Path ([IO.Path]::GetDirectoryName($wavPath)) (".glossolalia-" + [guid]::NewGuid().ToString("N") + ".wav")
        try {
            & $ffmpegExe -hide_banner -nostdin -n -i $audioPath -vn -ac 1 -ar 16000 -c:a pcm_s16le $tempWav
            if ($LASTEXITCODE -ne 0) {
                Write-Host "Преобразование не удалось."
                continue
            }

            [IO.File]::Move($tempWav, $wavPath)
        } catch {
            Write-Host "Ошибка преобразования: $($_.Exception.Message)"
            continue
        } finally {
            if (Test-Path -LiteralPath $tempWav) { Remove-Item -LiteralPath $tempWav -Force }
        }
    }

    $sb = New-Object System.Text.StringBuilder
    [void]$sb.AppendLine("=== КОМБИНИРОВАННЫЙ ФОНЕТИЧЕСКИЙ АНАЛИЗ Glossolalia Explorer ($($languages.Count) режимов) ===")
    [void]$sb.AppendLine("Файл: $audioPath")
    [void]$sb.AppendLine("Модель: $Model")
    [void]$sb.AppendLine("Фонетический эксперимент: язык и перевод автоматически не определяются")
    [void]$sb.AppendLine("Дата: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
    [void]$sb.AppendLine("")

    $processedLanguages = 0
    $unavailableLanguages = 0
    foreach ($lang in $languages) {
        $code = $lang.code
        $name = $lang.name
        $tempTs = $null

        [void]$sb.AppendLine("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        [void]$sb.AppendLine("ЯЗЫК: $name ($code)")
        [void]$sb.AppendLine("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        [void]$sb.AppendLine("")

        $languageArgument = $code
        if ($lang.profile) {
            $status = "ЭКСПЕРИМЕНТАЛЬНЫЙ, НЕОФИЦИАЛЬНЫЙ ПРОФИЛЬ"
            Write-Host "$name ($code): $status" -ForegroundColor Yellow
            Write-Host $lang.note -ForegroundColor Yellow
            [void]$sb.AppendLine($status)
            [void]$sb.AppendLine("Пользовательский набор фонем; отдельная обученная языковая модель не установлена")
            [void]$sb.AppendLine($lang.note)
            [void]$sb.AppendLine("Этот режим не определяет язык записи; оценки p не являются вероятностью языка")
            [void]$sb.AppendLine("")

            # Allosaurus принимает путь к пользовательскому набору вместо кода языка
            $languageArgument = Join-Path $profileDirectory $lang.profile
            if ($profileInfo.ContainsKey($code)) {
                $info = $profileInfo[$code]
                [void]$sb.AppendLine("Звуков в профиле: $($info.Phones.Count); доступно модели: $($info.Supported.Count)")
                if ($info.Unsupported.Count) {
                    [void]$sb.AppendLine("Модель не поддерживает и не распознаёт: $($info.Unsupported -join ', ')")
                    [void]$sb.AppendLine("Эти символы сохранены в профиле; автоматическая замена отключена")
                }
                [void]$sb.AppendLine("")
            }
        }

        if ($unavailableModes.ContainsKey($code)) {
            Write-Host "Пропущено: $name ($code): $($unavailableModes[$code])" -ForegroundColor Yellow
            [void]$sb.AppendLine("Пропущено: $($unavailableModes[$code])")
            [void]$sb.AppendLine("")
            $unavailableLanguages++
            continue
        }

        try {
            $tempTs = [System.IO.Path]::GetTempFileName()

            Write-Host "Распознавание: $name ($code), таймстампы и top-3..."
            & $pythonExe -B -X utf8 -m allosaurus.run --model $Model -i $wavPath --lang $languageArgument --timestamp=True --topk=3 --output $tempTs

            if ($LASTEXITCODE -ne 0) {
                Write-Host "Ошибка распознавания: $name ($code). Переход к следующему режиму."
                [void]$sb.AppendLine("Ошибка распознавания, код завершения: $LASTEXITCODE")
                [void]$sb.AppendLine("")
                continue
            }

            $tsLines = @(Get-Content -LiteralPath $tempTs -Encoding UTF8 -ErrorAction Stop | Where-Object { $_.Trim() -ne "" })

            if ($tsLines.Count -eq 0) {
                [void]$sb.AppendLine("Фонемы не обнаружены")
            }

            $prevEnd = -1.0
            $blockPhones = @()

            foreach ($line in $tsLines) {
                $parts = $line.Trim() -split '\s+'
                if ($parts.Count -lt 3) { continue }

                $start = [double]::Parse($parts[0], [Globalization.CultureInfo]::InvariantCulture)
                $dur = [double]::Parse($parts[1], [Globalization.CultureInfo]::InvariantCulture)
                $phone = $parts[2]

                $gap = 0.0
                if ($prevEnd -gt 0) {
                    $gap = $start - $prevEnd
                }

                # Паузы
                if ($gap -ge 0.25) {
                    # Закрываем блок
                    if ($blockPhones.Count -gt 0) {
                        [void]$sb.AppendLine("  -> $($blockPhones -join ' ')")
                        [void]$sb.AppendLine("")
                    }
                    [void]$sb.AppendLine("  === ПАУЗА $([math]::Round($gap, 3))с ===")
                    [void]$sb.AppendLine("")
                    $blockPhones = @()
                } elseif ($gap -ge 0.08) {
                    $blockPhones += "[...]"
                }

                # Top-k для этого звука
                $chosenProb = $null
                $alt1 = ""
                $alt1Prob = 0.0
                $alt2 = ""
                $alt2Prob = 0.0

                # При совместном запросе top-k находится в той же строке, что и таймстамп
                $matches = [regex]::Matches($line, '(\S+)\s+\(([0-9.]+)\)')
                $cands = @()
                foreach ($m in $matches) {
                    $cands += @{ phone = $m.Groups[1].Value; prob = [double]$m.Groups[2].Value }
                }
                if ($cands.Count -ge 1) { $chosenProb = $cands[0].prob }
                if ($cands.Count -ge 2) { $alt1 = $cands[1].phone; $alt1Prob = $cands[1].prob }
                if ($cands.Count -ge 3) { $alt2 = $cands[2].phone; $alt2Prob = $cands[2].prob }

                # Маркер уверенности
                $confMark = ""
                $probText = "н/д"
                if ($null -ne $chosenProb) {
                    $probText = [math]::Round($chosenProb, 3)
                    if ($chosenProb -lt 0.30) {
                        $confMark = " !!!"
                    } elseif ($chosenProb -lt 0.50) {
                        $confMark = " ?"
                    }
                }

                # Альтернативы
                $altStr = ""
                if ($alt1 -ne "" -and $alt1 -ne "<blk>") {
                    $altStr = "  alt: $alt1 ($([math]::Round($alt1Prob, 3)))"
                    if ($alt2 -ne "" -and $alt2 -ne "<blk>") {
                        $altStr += ", $alt2 ($([math]::Round($alt2Prob, 3)))"
                    }
                }

                [void]$sb.AppendLine("[$([math]::Round($start, 3))s] $phone (p=$probText)$confMark$altStr")
                $blockPhones += $phone

                $prevEnd = $start + $dur
            }

            # Последний блок
            if ($blockPhones.Count -gt 0) {
                [void]$sb.AppendLine("  -> $($blockPhones -join ' ')")
                [void]$sb.AppendLine("")
            }

            $processedLanguages++
            [void]$sb.AppendLine("--- конец $name ---")
            [void]$sb.AppendLine("")
        } catch {
            Write-Host "Ошибка режима $name ($code): $($_.Exception.Message)"
            [void]$sb.AppendLine("Ошибка обработки: $($_.Exception.Message)")
            [void]$sb.AppendLine("")
        } finally {
            if ($null -ne $tempTs) {
                Remove-Item -LiteralPath $tempTs -Force -ErrorAction SilentlyContinue
            }
        }
    }

    # Сводка
    [void]$sb.AppendLine("")
    [void]$sb.AppendLine("=== СВОДКА ===")
    [void]$sb.AppendLine("")
    [void]$sb.AppendLine("!!! = низкая уверенность (<0.30)")
    [void]$sb.AppendLine("? = средняя уверенность (0.30-0.50)")
    [void]$sb.AppendLine("alt = альтернативные распознавания")
    [void]$sb.AppendLine("н/д = оценка уверенности недоступна")
    [void]$sb.AppendLine("[...] = промежуток 0.08-0.25с между распознанными звуками")
    [void]$sb.AppendLine("=== ПАУЗА === = промежуток от 0.25с между распознанными звуками")

    [void]$sb.AppendLine("Режимов обработано: $processedLanguages из $($languages.Count)")
    [void]$sb.AppendLine("Недоступных режимов: $unavailableLanguages")

    # Запись (UTF-8 без BOM)
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($combinedPath, $sb.ToString(), $utf8NoBom)

    Write-Host ""
    Write-Host "Готово! Один файл со всем: $combinedPath"
    Write-Host "        ██" -ForegroundColor Green
    Write-Host "       ██" -ForegroundColor Green
    Write-Host "  ██  ██" -ForegroundColor Green
    Write-Host "   ████" -ForegroundColor Green
    Write-Host "    ██" -ForegroundColor Green
}
