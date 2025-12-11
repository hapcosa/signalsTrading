# windows.ps1 - Script optimizado para trading bot
param(
    [switch]$InstallService,
    [switch]$UninstallService,
    [switch]$StartService,
    [switch]$StopService,
    [switch]$CheckStatus
)

# Configuracion
$ScriptDirectory = $PSScriptRoot
$ServiceName = "TradingBotService"
$PythonExe = "python.exe"
$MainScript = "main.py"
$LogDir = Join-Path $ScriptDirectory "logs"
$LogFile = Join-Path $LogDir "trading_bot_$(Get-Date -Format 'yyyyMMdd').log"
$PIDFile = Join-Path $ScriptDirectory "trading_bot.pid"

# Crear directorio de logs si no existe
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

# Funcion para escribir logs
function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $Timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $LogMessage = "[$Timestamp] [$Level] $Message"
    Add-Content -Path $LogFile -Value $LogMessage -Encoding UTF8
    Write-Host $LogMessage
}

# Funcion para verificar si el bot ya esta corriendo
function Test-BotRunning {
    $pid = $null
    if (Test-Path $PIDFile) {
        $pid = Get-Content $PIDFile -ErrorAction SilentlyContinue
    }

    if ($pid) {
        try {
            $process = Get-Process -Id $pid -ErrorAction SilentlyContinue
            if ($process -and $process.ProcessName -like "*python*") {
                $cmdLine = (Get-WmiObject Win32_Process -Filter "ProcessId = $pid").CommandLine
                if ($cmdLine -like "*$MainScript*") {
                    return $true, $pid
                }
            }
        } catch {
            # PID invalido
        }
    }

    # Buscar por linea de comandos
    $processes = Get-WmiObject Win32_Process | Where-Object {
        $_.CommandLine -like "*$MainScript*" -and $_.Name -like "*python*"
    }

    if ($processes) {
        $pid = $processes[0].ProcessId
        $pid | Out-File -FilePath $PIDFile -Encoding UTF8
        return $true, $pid
    }

    return $false, $null
}

# Funcion para instalar como servicio con NSSM
function Install-TradingService {
    Write-Log "Instalando servicio $ServiceName..."

    # Verificar NSSM
    $nssmPath = Join-Path $ScriptDirectory "nssm.exe"
    if (-not (Test-Path $nssmPath)) {
        Write-Log "Descargando NSSM..." "WARNING"
        # Descargar NSSM si no existe
        $nssmUrl = "https://nssm.cc/release/nssm-2.24.zip"
        $tempZip = Join-Path $env:TEMP "nssm.zip"
        Invoke-WebRequest -Uri $nssmUrl -OutFile $tempZip

        # Extraer
        $tempDir = Join-Path $env:TEMP "nssm"
        Expand-Archive -Path $tempZip -DestinationPath $tempDir -Force

        # Copiar nssm.exe
        $nssmSource = Get-ChildItem -Path $tempDir -Recurse -Filter "nssm.exe" | Select-Object -First 1
        Copy-Item -Path $nssmSource.FullName -Destination $nssmPath -Force
    }

    # Instalar servicio
    & $nssmPath install $ServiceName $PythonExe "$ScriptDirectory\$MainScript"
    & $nssmPath set $ServiceName AppDirectory $ScriptDirectory
    & $nssmPath set $ServiceName DisplayName "Trading Bot Service"
    & $nssmPath set $ServiceName Description "Bot de Trading Automatizado para BingX y Bybit"
    & $nssmPath set $ServiceName Start SERVICE_AUTO_START
    & $nssmPath set $ServiceName AppStdout (Join-Path $LogDir "service_stdout.log")
    & $nssmPath set $ServiceName AppStderr (Join-Path $LogDir "service_stderr.log")

    # Configurar entorno
    & $nssmPath set $ServiceName AppEnvironmentExtra "PYTHONUTF8=1"

    Write-Log "Servicio instalado. Usa: windows.ps1 -StartService" "INFO"
}

# Funcion para iniciar el bot directamente (sin servicio)
function Start-TradingBot {
    $isRunning, $pid = Test-BotRunning

    if ($isRunning) {
        Write-Log "El bot ya esta corriendo (PID: $pid)" "WARNING"
        return $false
    }

    Write-Log "Iniciando bot de trading..." "INFO"
    Write-Log "Directorio: $ScriptDirectory" "INFO"
    Write-Log "Script: $MainScript" "INFO"

    # Configurar encoding
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8

    # Cambiar al directorio del script
    Set-Location $ScriptDirectory

    # Iniciar el bot en segundo plano
    $processStartInfo = @{
        FileName = $PythonExe
        Arguments = $MainScript
        WorkingDirectory = $ScriptDirectory
        RedirectStandardOutput = (Join-Path $LogDir "bot_output.log")
        RedirectStandardError = (Join-Path $LogDir "bot_error.log")
        UseShellExecute = $false
        CreateNoWindow = $true
    }

    $process = Start-Process @processStartInfo -PassThru

    # Guardar PID
    $process.Id | Out-File -FilePath $PIDFile -Encoding UTF8

    Write-Log "Bot iniciado con PID: $($process.Id)" "INFO"
    Write-Log "Logs en: $LogDir" "INFO"

    return $true
}

# Funcion para detener el bot
function Stop-TradingBot {
    $isRunning, $pid = Test-BotRunning

    if (-not $isRunning) {
        Write-Log "El bot no esta corriendo" "WARNING"
        return $false
    }

    Write-Log "Deteniendo bot (PID: $pid)..." "INFO"

    try {
        Stop-Process -Id $pid -Force -ErrorAction Stop
        Write-Log "Bot detenido exitosamente" "INFO"

        # Eliminar archivo PID
        if (Test-Path $PIDFile) {
            Remove-Item $PIDFile -Force
        }

        return $true
    } catch {
        Write-Log "Error deteniendo bot: $_" "ERROR"
        return $false
    }
}

# Funcion para verificar estado
function Get-BotStatus {
    $isRunning, $pid = Test-BotRunning

    if ($isRunning) {
        Write-Log "BOT CORRIENDO (PID: $pid)" "INFO"

        # Verificar memoria y CPU
        try {
            $process = Get-Process -Id $pid -ErrorAction SilentlyContinue
            if ($process) {
                $cpu = "{0:N1}" -f $process.CPU
                $mem = "{0:N1}" -f ($process.WorkingSet64 / 1MB)
                $uptime = (Get-Date) - $process.StartTime

                Write-Log "   Uptime: $($uptime.ToString('dd\.hh\:mm\:ss'))" "INFO"
                Write-Log "   Memoria: ${mem}MB | CPU: ${cpu}%" "INFO"
            }
        } catch {
            Write-Log "   No se pudo obtener informacion detallada" "WARNING"
        }

        # Verificar ultimos logs
        $recentLogs = Get-Content $LogFile -Tail 5 -ErrorAction SilentlyContinue
        if ($recentLogs) {
            Write-Log "   Ultimas 5 lineas del log:" "INFO"
            $recentLogs | ForEach-Object { Write-Log "   $_" "INFO" }
        }

        return $true
    } else {
        Write-Log "BOT DETENIDO" "INFO"
        return $false
    }
}

# Menu principal
Write-Log "=== Trading Bot Manager ===" "INFO"
Write-Log "Directorio: $ScriptDirectory" "INFO"

if ($InstallService) {
    Install-TradingService
}
elseif ($UninstallService) {
    Write-Log "Para desinstalar servicio: sc delete $ServiceName" "INFO"
}
elseif ($StartService) {
    Start-TradingBot
}
elseif ($StopService) {
    Stop-TradingBot
}
elseif ($CheckStatus) {
    Get-BotStatus
}
else {
    # Modo interactivo
    Write-Host ""
    Write-Host "=== Trading Bot Manager ===" -ForegroundColor Cyan
    Write-Host "1. Iniciar bot (sesion actual)"
    Write-Host "2. Detener bot"
    Write-Host "3. Verificar estado"
    Write-Host "4. Instalar como servicio Windows (ejecuta sin sesion)"
    Write-Host "5. Salir"

    $choice = Read-Host "Selecciona una opcion (1-5)"

    switch ($choice) {
        "1" {
            if (Start-TradingBot) {
                Write-Host ""
                Write-Host "Bot iniciado. Puedes cerrar esta ventana." -ForegroundColor Green
                Write-Host "Para ver logs: Get-Content '$LogFile' -Tail 20 -Wait" -ForegroundColor Yellow
            }
        }
        "2" { Stop-TradingBot }
        "3" { Get-BotStatus }
        "4" { Install-TradingService }
        "5" { exit }
        default { Write-Host "Opcion invalida" -ForegroundColor Red }
    }
}