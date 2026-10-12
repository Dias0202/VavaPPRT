# Sobe o X9 no Windows. Uso:  .\run.ps1          (roda o bot)
#                            .\run.ps1 check    (só valida a configuração)
#
# Na primeira execução cria a venv (Python 3.11), instala as dependências e
# copia o .env de exemplo.
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $py)) {
    Write-Host "Criando venv do bot (Python 3.11)..." -ForegroundColor Cyan
    py -3.11 -m venv (Join-Path $root ".venv")
    & $py -m pip install --upgrade pip --quiet
    & $py -m pip install -r (Join-Path $root "requirements.txt")
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        Write-Host "GPU NVIDIA detectada: instalando cuBLAS/cuDNN para o Whisper..." -ForegroundColor Cyan
        & $py -m pip install -r (Join-Path $root "requirements-gpu.txt")
    }
}
if (-not (Test-Path (Join-Path $root ".env"))) {
    Copy-Item (Join-Path $root ".env.example") (Join-Path $root ".env")
    Write-Host "bot\.env criado — preencha DISCORD_TOKEN, DATABASE_URL e GEMINI_API_KEY." -ForegroundColor Yellow
    exit 1
}

Push-Location $root
try { & $py -m x9 @args } finally { Pop-Location }
