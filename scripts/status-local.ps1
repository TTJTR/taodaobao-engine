. (Join-Path $PSScriptRoot "local-common.ps1")
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$values = Get-LocalEnv (Join-Path $projectRoot ".env")
$listenAddress = Get-EnvOrDefault $values "HOST" "127.0.0.1"
$probeAddress = if ($listenAddress -eq "0.0.0.0") { "127.0.0.1" } else { $listenAddress }
$backendPort = Get-EnvOrDefault $values "BACKEND_PORT" "8000"

Push-Location $projectRoot
try {
    docker compose ps
    Write-Host ""
    try {
        $health = Invoke-RestMethod -Uri "http://${probeAddress}:$backendPort/api/v1/health" -TimeoutSec 5
        $health.data | ConvertTo-Json -Depth 8
    } catch {
        Write-Warning "健康接口暂不可达：$($_.Exception.Message)"
    }
} finally {
    Pop-Location
}
