Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-LocalEnv {
    param([string]$Path = (Join-Path $PSScriptRoot "..\.env"))
    $values = @{}
    if (-not (Test-Path -LiteralPath $Path)) { return $values }
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
        $name, $value = $line -split '=', 2
        $values[$name.Trim()] = $value.Trim().Trim('"').Trim("'")
    }
    return $values
}

function Set-LocalEnvValue {
    param([string]$Path, [string]$Name, [string]$Value)
    $content = Get-Content -LiteralPath $Path -Raw
    $escapedName = [regex]::Escape($Name)
    if ($content -match "(?m)^$escapedName=") {
        $content = [regex]::Replace($content, "(?m)^$escapedName=.*$", "$Name=$Value")
    } else {
        $content = $content.TrimEnd() + "`r`n$Name=$Value`r`n"
    }
    [System.IO.File]::WriteAllText($Path, $content, [System.Text.UTF8Encoding]::new($false))
}

function New-RandomHex {
    param([int]$Bytes = 32)
    $buffer = [byte[]]::new($Bytes)
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($buffer)
    return [Convert]::ToHexString($buffer).ToLowerInvariant()
}

function New-FernetKey {
    $buffer = [byte[]]::new(32)
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($buffer)
    return [Convert]::ToBase64String($buffer).Replace('+', '-').Replace('/', '_')
}

function Get-EnvOrDefault {
    param([hashtable]$Values, [string]$Name, [string]$Default)
    if ($Values.ContainsKey($Name) -and $Values[$Name]) { return $Values[$Name] }
    return $Default
}
