#Requires -Version 5.1

function Get-ProfileInfo {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string[]]$SupportedPhones
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Файл профиля не найден: $Path" }
    $bytes = [IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 239 -and $bytes[1] -eq 187 -and $bytes[2] -eq 191) {
        throw "Профиль должен быть UTF-8 без BOM: $Path"
    }

    $utf8 = New-Object Text.UTF8Encoding($false, $true)
    $text = $utf8.GetString($bytes)
    $phones = @($text -split '\r?\n')
    if ($phones.Count -gt 1 -and $phones[-1] -eq "") { $phones = @($phones[0..($phones.Count - 2)]) }
    $seen = New-Object 'System.Collections.Generic.HashSet[string]'
    foreach ($phone in $phones) {
        if (-not $phone -or $phone -match '\s' -or $phone -eq "<blk>") {
            throw "Ожидался один знак IPA на строку без пробелов и <blk>: $Path"
        }
        if (-not $seen.Add($phone)) { throw "Повтор фонемы '$phone': $Path" }
    }

    # Сравнение регистрозависимое, поскольку I и i могут обозначать разные звуки модели
    $supportedSet = New-Object 'System.Collections.Generic.HashSet[string]'
    foreach ($phone in $SupportedPhones) { [void]$supportedSet.Add($phone) }
    $supported = @($phones | Where-Object { $supportedSet.Contains($_) })
    $unsupported = @($phones | Where-Object { -not $supportedSet.Contains($_) })
    if (-not $supported.Count) { throw "Ни один звук профиля не поддерживается моделью: $Path" }

    [pscustomobject]@{ Path=$Path; Phones=$phones; Supported=$supported; Unsupported=$unsupported }
}
