param([switch]$NoBuild)

. (Join-Path $PSScriptRoot "local-common.ps1")
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$envPath = Join-Path $projectRoot ".env"
$examplePath = Join-Path $projectRoot ".env.example"

if (-not (Test-Path -LiteralPath $envPath)) {
    Copy-Item -LiteralPath $examplePath -Destination $envPath
    $databasePassword = New-RandomHex 24
    $localPassword = New-RandomHex 12
    Set-LocalEnvValue $envPath "POSTGRES_PASSWORD" $databasePassword
    Set-LocalEnvValue $envPath "APP_DATABASE_URL" "postgresql+asyncpg://taodaobao:$databasePassword@127.0.0.1:5432/taodaobao"
    Set-LocalEnvValue $envPath "APP_SESSION_SECRET" (New-RandomHex 32)
    Set-LocalEnvValue $envPath "APP_SECRET_ENCRYPTION_KEY" (New-FernetKey)
    Set-LocalEnvValue $envPath "APP_LOCAL_ADMIN_PASSWORD" $localPassword
    Write-Host "已创建 .env 并生成本地密钥。"
    Write-Host "本地管理员：admin"
    Write-Host "本地管理员临时密码：$localPassword"
    Write-Host "请妥善保存并可在 .env 中修改。"
}

Push-Location $projectRoot
try {
    docker info *> $null
    if ($LASTEXITCODE -ne 0) { throw "Docker Desktop 尚未启动或 Linux 容器引擎不可用。" }
    docker compose config --quiet
    if ($LASTEXITCODE -ne 0) { throw "docker compose 配置校验失败。" }
    if ($NoBuild) {
        docker compose up -d
    } else {
        docker compose up -d --build
    }
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose 启动失败。" }
    $values = Get-LocalEnv $envPath
    $listenAddress = Get-EnvOrDefault $values "HOST" "127.0.0.1"
    $browserAddress = if ($listenAddress -eq "0.0.0.0") { "<本机局域网IP>" } else { $listenAddress }
    $frontendPort = Get-EnvOrDefault $values "FRONTEND_PORT" "3000"
    $backendPort = Get-EnvOrDefault $values "BACKEND_PORT" "8000"
    Write-Host ""
    Write-Host "淘到宝引擎正在启动："
    Write-Host "前端地址       http://${browserAddress}:$frontendPort"
    Write-Host "API 地址        http://${browserAddress}:$backendPort/api/v1"
    Write-Host "API 文档        http://${browserAddress}:$backendPort/docs"
    Write-Host "健康检查        http://${browserAddress}:$backendPort/api/v1/health"
    Write-Host "运行 scripts/status-local.ps1 查看健康状态。"
} finally {
    Pop-Location
}
