# Deixa o X9 ligado sozinho neste PC: registra a tarefa agendada "X9 Discord Bot"
# que sobe o bot (oculto) quando você faz logon e reinicia se ele cair.
#
#   .\install-autostart.ps1            instala/atualiza e já inicia
#   .\install-autostart.ps1 -Remove    remove a tarefa
#
# Log: bot\logs\x9.log
param([switch]$Remove)
$ErrorActionPreference = "Stop"
$name = "X9 Discord Bot"
$root = $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\pythonw.exe"   # pythonw: sem janela de console

if ($Remove) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Tarefa '$name' removida." -ForegroundColor Yellow
    exit 0
}
if (-not (Test-Path $py)) { throw "Venv não encontrada: rode .\run.ps1 uma vez antes." }
if (-not (Test-Path (Join-Path $root ".env"))) { throw "bot\.env não existe: rode .\run.ps1 antes." }

$action = New-ScheduledTaskAction -Execute $py -Argument "-m x9" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description "Bot X9 do Discord (VavaPPRT)" -Force | Out-Null
Start-ScheduledTask -TaskName $name
Write-Host "Tarefa '$name' instalada e iniciada. Log: $root\logs\x9.log" -ForegroundColor Green
