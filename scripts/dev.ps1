$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
    Write-Host '已创建 .env，请填写 CHAT_BASE_URL / CHAT_MODEL / CHAT_API_KEY。'
}
uv sync --locked
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
docker compose up -d --wait
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run --no-sync python scripts/init_db.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$env:TIKTOKEN_CACHE_DIR = "$PWD\.cache\tiktoken"
$env:PYTHONIOENCODING = 'utf-8'
$taskMakeBin = Join-Path $PWD '.cache/bin'
if (Test-Path -LiteralPath (Join-Path $taskMakeBin 'make.exe')) {
    $env:PATH = "$taskMakeBin;" + $env:PATH
}
uv run --no-sync python scripts/mcp_services.py up
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
try { uv run --no-sync uvicorn app.main:app --host 127.0.0.1 --port 8000 }
finally { uv run --no-sync python scripts/mcp_services.py down }
exit $LASTEXITCODE
