$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
function New-Secret {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return ([BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
}
$content = if (Test-Path -LiteralPath '.env') { Get-Content -Raw -LiteralPath '.env' } else { Get-Content -Raw -LiteralPath '.env.example' }
if ($content -notmatch '(?m)^WPPCONNECT_WEBHOOK_SECRET=') { $content += "`nWPPCONNECT_WEBHOOK_SECRET=replace-with-random-secret`n" }
foreach ($key in @('POSTGRES_PASSWORD', 'DJANGO_SECRET_KEY', 'REDIS_PASSWORD', 'WPPCONNECT_SECRET_KEY', 'WPPCONNECT_WEBHOOK_SECRET')) {
    $content = $content.Replace("${key}=replace-with-random-secret", "${key}=$(New-Secret)")
}
[System.IO.File]::WriteAllText((Join-Path (Get-Location) '.env'), $content, (New-Object System.Text.UTF8Encoding($false)))
Write-Host 'Initialized .env. Existing credentials preserved; missing secrets generated. Keep this file private.'
