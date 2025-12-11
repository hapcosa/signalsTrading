# deploy_windows.ps1 - Script de implementacion
param(
    [switch]$ScheduleTask,
    [switch]$InstallService,
    [switch]$QuickStart
)

$ScriptDir = $PSScriptRoot
$BotDir = $ScriptDir
$TaskName = "TradingBot"
$TaskDescription = "Ejecuta bot de trading automaticamente al inicio"

Write-Host "=== DEPLOY TRADING BOT ===" -ForegroundColor Cyan
Write-Host "Directorio: $BotDir" -ForegroundColor Yellow

# Verificar requisitos
Write-Host ""
Write-Host "Verificando requisitos..." -ForegroundColor Cyan

# 1. Verificar Python
try {
    $pythonVersion = python --version 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Python encontrado: $pythonVersion" -ForegroundColor Green
    } else {
        Write-Host "Python no encontrado. Instala Python 3.8+ primero." -ForegroundColor Red
        exit 1
    }
} catch {
    Write-Host "Python no encontrado. Instala Python 3.8+ primero." -ForegroundColor Red
    exit 1
}

# 2. Verificar dependencias
Write-Host ""
Write-Host "Instalando/verificando dependencias..." -ForegroundColor Cyan
Set-Location $BotDir

# Instalar pip si no existe
try {
    python -m pip --version 2>&1 | Out-Null
} catch {
    Write-Host "Instalando pip..." -ForegroundColor Yellow
    python -m ensurepip --upgrade
}

# Instalar dependencias
$requirementsFile = Join-Path $BotDir "requirements.txt"
if (Test-Path $requirementsFile) {
    Write-Host "Instalando desde requirements.txt..." -ForegroundColor Yellow
    python -m pip install -r requirements.txt
} else {
    # Crear requirements.txt si no existe
    $requirements = @"
python-telegram-bot==20.3
python-dotenv==1.0.0
requests==2.31.0
hmac==0.0.0
"@
    $requirements | Out-File -FilePath $requirementsFile -Encoding UTF8
    Write-Host "requirements.txt creado" -ForegroundColor Green
    python -m pip install -r requirements.txt
}

# 3. Verificar archivos de configuracion
Write-Host ""
Write-Host "Verificando configuracion..." -ForegroundColor Cyan

# Verificar .env
$envFile = Join-Path $BotDir ".env"
if (-not (Test-Path $envFile)) {
    Write-Host ".env no encontrado. Crea uno desde .env.example" -ForegroundColor Yellow
    $envExample = Join-Path $BotDir ".env.example"
    if (Test-Path $envExample) {
        Copy-Item $envExample $envFile
        Write-Host ".env creado desde .env.example. Edita con tus credenciales." -ForegroundColor Green
    }
}

# Verificar config.json
$configFile = Join-Path $BotDir "config.json"
if (-not (Test-Path $configFile)) {
    Write-Host "config.json no encontrado. Este archivo es obligatorio." -ForegroundColor Red
    exit 1
}

# 4. OPCION: Crear tarea programada
if ($ScheduleTask) {
    Write-Host ""
    Write-Host "Creando tarea programada..." -ForegroundColor Cyan

    $action = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$BotDir\windows.ps1`" -StartService"

    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Principal $principal `
        -Settings $settings `
        -Description $TaskDescription `
        -Force

    Write-Host "Tarea programada creada: $TaskName" -ForegroundColor Green
    Write-Host "Se ejecutara automaticamente al inicio del sistema." -ForegroundColor Yellow

    # Iniciar ahora
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Tarea iniciada. El bot se ejecutara en segundo plano." -ForegroundColor Green
}

# 5. OPCIÓN: Instalar como servicio Windows
if ($InstallService) {
    Write-Host ""
    Write-Host "Instalando como servicio Windows..." -ForegroundColor Cyan

    # Ejecutar windows.ps1 con parametro de servicio
    & "$BotDir\windows.ps1" -InstallService

    Write-Host ""
    Write-Host "Para gestionar el servicio:" -ForegroundColor Yellow
    Write-Host "   Iniciar:    sc start TradingBotService" -ForegroundColor Gray
    Write-Host "   Detener:    sc stop TradingBotService" -ForegroundColor Gray
    Write-Host "   Estado:     sc query TradingBotService" -ForegroundColor Gray
    Write-Host "   Desinstalar: sc delete TradingBotService" -ForegroundColor Gray
}

# 6. OPCION: Inicio rapido
if ($QuickStart) {
    Write-Host ""
    Write-Host "Iniciando bot rapidamente..." -ForegroundColor Cyan

    # Crear directorio de logs
    $logDir = Join-Path $BotDir "logs"
    if (-not (Test-Path $logDir)) {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    }

    # Configurar entorno
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"

    # Iniciar bot
    $process = Start-Process python `
        -ArgumentList "main.py" `
        -WorkingDirectory $BotDir `
        -NoNewWindow `
        -RedirectStandardOutput "$logDir\bot_$(Get-Date -Format 'yyyyMMdd_HHmmss').log" `
        -RedirectStandardError "$logDir\bot_error_$(Get-Date -Format 'yyyyMMdd_HHmmss').log" `
        -PassThru

    Write-Host "Bot iniciado con PID: $($process.Id)" -ForegroundColor Green
    Write-Host "Ver logs en: $logDir" -ForegroundColor Yellow
    Write-Host "Puedes cerrar esta ventana SSH, el bot seguira corriendo." -ForegroundColor Cyan
}

# 7. Si no hay parametros, mostrar menu
if (-not ($ScheduleTask -or $InstallService -or $QuickStart)) {
    Write-Host ""
    Write-Host "OPCIONES DE IMPLEMENTACION:" -ForegroundColor Cyan
    Write-Host "1. Ejecutar deploy_windows.ps1 -QuickStart (inicio rapido)" -ForegroundColor Yellow
    Write-Host "2. Ejecutar deploy_windows.ps1 -ScheduleTask (tarea programada)" -ForegroundColor Yellow
    Write-Host "3. Ejecutar deploy_windows.ps1 -InstallService (servicio Windows)" -ForegroundColor Yellow
    Write-Host "4. Usar windows.ps1 interactivo (gestion manual)" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Recomendacion: Usa -ScheduleTask para ejecucion automatica." -ForegroundColor Green
}

Write-Host ""
Write-Host "=== DEPLOY COMPLETADO ===" -ForegroundColor Green