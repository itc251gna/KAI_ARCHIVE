[CmdletBinding()]
param(
    [switch]$StartStack,
    [switch]$SmokeTest
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

Write-Step "Python syntax checks"
py -3 -m py_compile app.py tools/test_wsdl.py

Write-Step "Docker Compose config checks"
docker compose -f docker-compose.local.yml config --quiet
docker compose -f docker-compose.remote.yml config --quiet

if ($StartStack) {
    Write-Step "Starting local replica"
    docker compose -f docker-compose.local.yml up -d --build
}

if ($SmokeTest) {
    Write-Step "HTTPS smoke tests"
    $Urls = @(
        "https://localhost:8443/login",
        "https://localhost:8443/guacamole/"
    )

    foreach ($Url in $Urls) {
        $Status = & curl.exe -k -s -o NUL -w "%{http_code}" $Url
        if ($LASTEXITCODE -ne 0) {
            throw "curl failed for $Url"
        }

        $StatusCode = [int]$Status
        if ($StatusCode -lt 200 -or $StatusCode -ge 400) {
            throw "Smoke test failed for $Url with HTTP $StatusCode"
        }

        Write-Host "$Url -> HTTP $StatusCode"
    }
}

Write-Step "Verification complete"
