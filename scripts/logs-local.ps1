param(
    [ValidateSet("all", "frontend", "app", "worker", "db", "redis", "open-enrich-sidecar")]
    [string]$Service = "all",
    [switch]$Follow,
    [int]$Tail = 200
)

. (Join-Path $PSScriptRoot "local-common.ps1")
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$arguments = @("compose", "logs", "--tail", "$Tail")
if ($Follow) { $arguments += "--follow" }
if ($Service -ne "all") { $arguments += $Service }
Push-Location $projectRoot
try { & docker $arguments } finally { Pop-Location }
