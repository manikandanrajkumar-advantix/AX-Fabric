param(
    [string]$Container = 'ax-postgres',
    [string]$MigrationDirectory = (Join-Path $PSScriptRoot '..\migrations')
)

$ErrorActionPreference = 'Stop'
$files = Get-ChildItem -LiteralPath $MigrationDirectory -File -Filter '*.sql' | Sort-Object Name
if (-not $files) { throw "No SQL migrations found in $MigrationDirectory" }
$containerEnvironment = docker inspect --format '{{json .Config.Env}}' $Container | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw "Unable to inspect database container $Container" }
$databaseUser = ($containerEnvironment | Where-Object { $_ -like 'POSTGRES_USER=*' }) -replace '^POSTGRES_USER=', ''
$databaseName = ($containerEnvironment | Where-Object { $_ -like 'POSTGRES_DB=*' }) -replace '^POSTGRES_DB=', ''
if (-not $databaseUser -or -not $databaseName) { throw 'Database user or database name is missing from the container environment' }

foreach ($file in $files) {
    if ($file.BaseName -notmatch '^\d{3}_[a-z0-9_]+$') { throw "Invalid migration filename: $($file.Name)" }
    $migrationId = $file.BaseName
    $checksum = (Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash.ToLowerInvariant()
    $existing = docker exec $Container psql -Atq -U $databaseUser -d $databaseName -c "SELECT checksum FROM platform.schema_migrations WHERE migration_id='$migrationId'"
    if ($LASTEXITCODE -ne 0) { throw "Unable to query migration $migrationId" }
    $existing = ($existing | Out-String).Trim()
    if ($existing -and $existing -ne 'managed-by-repository') {
        if ($existing -ne $checksum) { throw "Checksum mismatch for applied migration $migrationId" }
        Write-Host "verified $migrationId"
        continue
    }
    $remote = "/tmp/$($file.Name)"
    docker cp $file.FullName "${Container}:$remote"
    if ($LASTEXITCODE -ne 0) { throw "Unable to copy $migrationId" }
    try {
        if (-not $existing) {
            docker exec $Container psql -v ON_ERROR_STOP=1 -U $databaseUser -d $databaseName -f $remote
            if ($LASTEXITCODE -ne 0) { throw "Migration failed: $migrationId" }
        }
        docker exec $Container psql -v ON_ERROR_STOP=1 -U $databaseUser -d $databaseName -c "UPDATE platform.schema_migrations SET checksum='$checksum' WHERE migration_id='$migrationId'"
        if ($LASTEXITCODE -ne 0) { throw "Unable to register checksum for $migrationId" }
        Write-Host "applied $migrationId $checksum"
    }
    finally { docker exec $Container rm -f $remote | Out-Null }
}
