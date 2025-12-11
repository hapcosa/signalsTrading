# windows.ps1

# Obtener la ruta del script actual para usar rutas absolutas
$ScriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogPath = Join-Path $ScriptDirectory "execution_log.txt"
$OutputLogPath = Join-Path $ScriptDirectory "output_log.txt"

# Configurar encoding UTF-8
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

# Forzar el cambio al directorio del script
Set-Location $ScriptDirectory

# Registrar inicio con información de diagnóstico
$Timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
"[$Timestamp] ===== INICIANDO APPLICACION =====" | Out-File -FilePath $LogPath -Append -Encoding UTF8
"[$Timestamp] Directorio del script: $ScriptDirectory" | Out-File -FilePath $LogPath -Append -Encoding UTF8
"[$Timestamp] Directorio de trabajo actual: $(Get-Location)" | Out-File -FilePath $LogPath -Append -Encoding UTF8
"[$Timestamp] Usuario: $env:USERNAME" | Out-File -FilePath $LogPath -Append -Encoding UTF8

# Verificar entorno virtual - Método alternativo para SYSTEM
$VenvPath = Join-Path (Split-Path -Parent $ScriptDirectory) "venv"
$PythonExe = Join-Path $VenvPath "Scripts\python.exe"

"[$Timestamp] Buscando entorno virtual en: $VenvPath" | Out-File -FilePath $LogPath -Append -Encoding UTF8
"[$Timestamp] Python ejecutable: $PythonExe" | Out-File -FilePath $LogPath -Append -Encoding UTF8

if (Test-Path $PythonExe) {
    "[$Timestamp] Usando Python del entorno virtual: $PythonExe" | Out-File -FilePath $LogPath -Append -Encoding UTF8

    # Configurar variables de entorno para el entorno virtual
    $env:VIRTUAL_ENV = $VenvPath
    $env:PATH = "$(Join-Path $VenvPath 'Scripts');$env:PATH"

    "[$Timestamp] Entorno virtual configurado manualmente" | Out-File -FilePath $LogPath -Append -Encoding UTF8
} else {
    "[$Timestamp] ERROR: Python del entorno virtual no encontrado en $PythonExe" | Out-File -FilePath $LogPath -Append -Encoding UTF8
    "[$Timestamp] Verificando Python del sistema..." | Out-File -FilePath $LogPath -Append -Encoding UTF8

    # Intentar con Python del sistema
    try {
        $PythonVersion = python --version 2>&1
        if ($LASTEXITCODE -eq 0) {
            "[$Timestamp] Usando Python del sistema: $PythonVersion" | Out-File -FilePath $LogPath -Append -Encoding UTF8
        } else {
            "[$Timestamp] ERROR: Python no disponible" | Out-File -FilePath $LogPath -Append -Encoding UTF8
            exit 1
        }
    } catch {
        "[$Timestamp] ERROR: No se puede ejecutar python - $($_.Exception.Message)" | Out-File -FilePath $LogPath -Append -Encoding UTF8
        exit 1
    }
}

# Verificar que main.py existe
$MainScript = Join-Path (Split-Path -Parent $ScriptDirectory) "main.py"
"[$Timestamp] Buscando script: $MainScript" | Out-File -FilePath $LogPath -Append -Encoding UTF8
if (-not (Test-Path $MainScript)) {
    "[$Timestamp] ERROR: main.py no encontrado" | Out-File -FilePath $LogPath -Append -Encoding UTF8
    exit 1
}

# Ejecutar main.py usando el Python del entorno virtual si está disponible
"[$Timestamp] Ejecutando: $PythonExe main.py" | Out-File -FilePath $LogPath -Append -Encoding UTF8
try {
    $ParentDirectory = Split-Path -Parent $ScriptDirectory

    if (Test-Path $PythonExe) {
        # Usar el Python del entorno virtual directamente
        $Process = Start-Process -FilePath $PythonExe -ArgumentList "main.py" -WorkingDirectory $ParentDirectory -Wait -PassThru -NoNewWindow -RedirectStandardOutput $OutputLogPath -RedirectStandardError "$OutputLogPath.errors"
    } else {
        # Usar Python del sistema
        $Process = Start-Process -FilePath "python" -ArgumentList "main.py" -WorkingDirectory $ParentDirectory -Wait -PassThru -NoNewWindow -RedirectStandardOutput $OutputLogPath -RedirectStandardError "$OutputLogPath.errors"
    }

    "[$Timestamp] Script finalizado con código de salida: $($Process.ExitCode)" | Out-File -FilePath $LogPath -Append -Encoding UTF8

    if (Test-Path $OutputLogPath) {
        $LogSize = (Get-Item $OutputLogPath).Length
        "[$Timestamp] Log de output creado: $OutputLogPath ($LogSize bytes)" | Out-File -FilePath $LogPath -Append -Encoding UTF8
    }

    if (Test-Path "$OutputLogPath.errors") {
        $ErrorLogSize = (Get-Item "$OutputLogPath.errors").Length
        "[$Timestamp] Log de errores creado: $OutputLogPath.errors ($ErrorLogSize bytes)" | Out-File -FilePath $LogPath -Append -Encoding UTF8
        if ($ErrorLogSize -gt 0) {
            "[$Timestamp] Contenido de errores:" | Out-File -FilePath $LogPath -Append -Encoding UTF8
            Get-Content "$OutputLogPath.errors" | Out-File -FilePath $LogPath -Append -Encoding UTF8
        }
    }
} catch {
    "[$Timestamp] ERROR al ejecutar main.py: $($_.Exception.Message)" | Out-File -FilePath $LogPath -Append -Encoding UTF8
}

"[$Timestamp] ===== SCRIPT FINALIZADO =====" | Out-File -FilePath $LogPath -Append -Encoding UTF8