$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
function Invoke-Docker {
    & docker @args
    if ($LASTEXITCODE -ne 0) { throw "Docker check failed: $($args -join ' ')" }
}
Invoke-Docker compose config --quiet
Invoke-Docker compose ps --all
Invoke-Docker compose exec -T backend python manage.py check
Invoke-Docker compose exec -T backend python manage.py makemigrations --check --dry-run
Invoke-Docker compose exec -T backend python manage.py test config --verbosity 2
Invoke-Docker compose exec -T backend python manage.py test apps.accounts apps.whatsapp apps.conversations apps.notifications apps.ai_engine apps.products --verbosity 2
Invoke-Docker compose exec -T backend python scripts/verify_infrastructure.py
Invoke-Docker compose exec -T backend python scripts/verify_provider_contract.py
Invoke-Docker compose exec -T frontend npm run build
Write-Host 'Automated regression checks passed. Real-device WhatsApp pairing and live messaging need manual verification.'
