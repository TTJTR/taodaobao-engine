param([string]$ApiBase = "http://127.0.0.1:8000/api/v1")

. (Join-Path $PSScriptRoot "local-common.ps1")
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$values = Get-LocalEnv (Join-Path $projectRoot ".env")
$username = Get-EnvOrDefault $values "APP_LOCAL_ADMIN_USERNAME" "admin"
$password = Get-EnvOrDefault $values "APP_LOCAL_ADMIN_PASSWORD" ""
if (-not $password) { throw "APP_LOCAL_ADMIN_PASSWORD 未配置。" }

$webSession = [Microsoft.PowerShell.Commands.WebRequestSession]::new()
$headers = @{ "Idempotency-Key" = ([guid]::NewGuid().ToString("N")) }
$loginBody = @{ username = $username; password = $password } | ConvertTo-Json
Invoke-RestMethod -Uri "$ApiBase/auth/local/login" -Method Post -WebSession $webSession `
    -Headers $headers -ContentType "application/json" -Body $loginBody | Out-Null

$fixturePath = Join-Path $projectRoot "fixtures\local_demo\sources.json"
$items = Get-Content -LiteralPath $fixturePath -Raw | ConvertFrom-Json
foreach ($item in $items) {
    $headers = @{
        "Idempotency-Key" = ([guid]::NewGuid().ToString("N"))
        "X-Demo-Data" = "true"
    }
    $body = $item | ConvertTo-Json -Depth 5
    $result = Invoke-RestMethod -Uri "$ApiBase/sources/import-text" -Method Post `
        -WebSession $webSession -Headers $headers -ContentType "application/json" -Body $body
    Write-Host "已提交：$($item.title)；任务 $($result.data.job_id)"
}
Write-Host "示例资料走真实资料处理与 AI 提取流程，并标记 is_demo=true。"
