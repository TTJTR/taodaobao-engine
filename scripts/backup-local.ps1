param([string]$OutputDirectory = (Join-Path $PSScriptRoot "..\backups"))

. (Join-Path $PSScriptRoot "local-common.ps1")
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$resolvedOutput = [System.IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force -Path $resolvedOutput | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$databaseFile = Join-Path $resolvedOutput "taodaobao-$stamp.sql"
$filesArchive = Join-Path $resolvedOutput "taodaobao-files-$stamp.tar.gz"

Push-Location $projectRoot
try {
    $values = Get-LocalEnv (Join-Path $projectRoot ".env")
    $databaseUser = Get-EnvOrDefault $values "POSTGRES_USER" "taodaobao"
    $databaseName = Get-EnvOrDefault $values "POSTGRES_DB" "taodaobao"
    $sql = & docker compose exec -T db pg_dump -U $databaseUser $databaseName
    if ($LASTEXITCODE -ne 0) { throw "数据库备份失败。" }
    [System.IO.File]::WriteAllLines($databaseFile, $sql, [System.Text.UTF8Encoding]::new($false))
    docker run --rm -v taodaobao-local_local_files:/data:ro -v "${resolvedOutput}:/backup" alpine `
        tar -czf "/backup/$(Split-Path -Leaf $filesArchive)" -C /data .
    if ($LASTEXITCODE -ne 0) { throw "文件卷备份失败。" }
    Write-Host "数据库备份：$databaseFile"
    Write-Host "文件卷备份：$filesArchive"
} finally {
    Pop-Location
}
