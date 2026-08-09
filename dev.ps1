# Sobe o ambiente de desenvolvimento completo (backend FastAPI + frontend Vite).
# Uso:  .\dev.ps1
#
# Na primeira execução cria a venv, instala as dependências dos dois lados e
# copia os .env de exemplo. Depois disso, só sobe os dois servidores.

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$py = Join-Path $backend ".venv\Scripts\python.exe"

# --- Backend ---------------------------------------------------------------
if (-not (Test-Path $py)) {
    Write-Host "Criando venv do backend..." -ForegroundColor Cyan
    python -m venv (Join-Path $backend ".venv")
    & $py -m pip install --upgrade pip --quiet
    & $py -m pip install -r (Join-Path $backend "requirements.txt")
}
if (-not (Test-Path (Join-Path $backend ".env"))) {
    Copy-Item (Join-Path $backend ".env.example") (Join-Path $backend ".env")
    Write-Host "backend\.env criado a partir do exemplo — preencha HENRIKDEV_API_KEY." -ForegroundColor Yellow
}

# --- Frontend --------------------------------------------------------------
if (-not (Test-Path (Join-Path $frontend "node_modules"))) {
    Write-Host "Instalando dependências do frontend..." -ForegroundColor Cyan
    Push-Location $frontend
    try { npm install --no-audit --no-fund } finally { Pop-Location }
}
if (-not (Test-Path (Join-Path $frontend ".env"))) {
    Copy-Item (Join-Path $frontend ".env.example") (Join-Path $frontend ".env")
}

# --- Sobe os dois ----------------------------------------------------------
Write-Host "`nBackend  -> http://localhost:8000  (docs em /docs)" -ForegroundColor Green
Write-Host "Frontend -> http://localhost:5173`n" -ForegroundColor Green

$api = Start-Process -FilePath $py `
    -ArgumentList "-m", "uvicorn", "app.main:app", "--reload", "--port", "8000" `
    -WorkingDirectory $backend -PassThru -NoNewWindow

try {
    Push-Location $frontend
    npm run dev
} finally {
    Pop-Location
    if ($api -and -not $api.HasExited) { Stop-Process -Id $api.Id -Force }
}
