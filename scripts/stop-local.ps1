. (Join-Path $PSScriptRoot "local-common.ps1")
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $projectRoot
try {
    docker compose down
    if ($LASTEXITCODE -ne 0) { throw "停止失败。" }
    Write-Host "服务已停止；PostgreSQL、Redis 和上传文件卷均已保留。"
} finally {
    Pop-Location
}
