. (Join-Path $PSScriptRoot "local-common.ps1")
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$values = Get-LocalEnv (Join-Path $projectRoot ".env")
$listenAddress = Get-EnvOrDefault $values "HOST" "127.0.0.1"
$probeAddress = if ($listenAddress -eq "0.0.0.0") { "127.0.0.1" } else { $listenAddress }
$backendPort = Get-EnvOrDefault $values "BACKEND_PORT" "8000"
$frontendPort = Get-EnvOrDefault $values "FRONTEND_PORT" "3000"

Push-Location $projectRoot
try {
    docker compose ps
    Write-Host ""
    Write-Host "前端地址：  http://${probeAddress}:$frontendPort"
    Write-Host "API 地址：   http://${probeAddress}:$backendPort/api/v1"
    Write-Host "API 文档：   http://${probeAddress}:$backendPort/docs"
    Write-Host "健康检查：   http://${probeAddress}:$backendPort/api/v1/health"
    Write-Host ""
    try {
        $health = Invoke-RestMethod -Uri "http://${probeAddress}:$backendPort/api/v1/health" -TimeoutSec 5
        $health.data | ConvertTo-Json -Depth 8
        Write-Host ""
        if ($health.data.status -eq "ok") {
            Write-Host "结论：核心服务与已启用 Provider 均正常。" -ForegroundColor Green
        } elseif ($health.data.service -eq "ok" -and $health.data.database -eq "ok" -and $health.data.redis -eq "ok") {
            Write-Host "结论：本地核心服务正常；未配置或不可用的可选 Provider 使状态降级。" -ForegroundColor Yellow
        } else {
            Write-Warning "结论：核心依赖存在异常，请查看 app 与 worker 日志。"
        }
    } catch {
        Write-Warning "健康接口暂不可达：$($_.Exception.Message)"
    }
} finally {
    Pop-Location
}
