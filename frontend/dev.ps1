# Sobe o dev server do frontend usando o Node do nodeenv (sem precisar de Node no sistema).
# Uso:  .\dev.ps1
$nodeBin = (Resolve-Path "$PSScriptRoot\..\.nodeenv\Scripts").Path
$env:Path = "$nodeBin;$env:Path"
Push-Location $PSScriptRoot
try {
    if (-not (Test-Path "node_modules")) {
        Write-Host "Instalando dependências..." -ForegroundColor Cyan
        npm install --no-audit --no-fund
        npm approve-scripts --allow-scripts-pending 2>$null
        npm rebuild esbuild 2>$null
    }
    npm run dev
} finally {
    Pop-Location
}
