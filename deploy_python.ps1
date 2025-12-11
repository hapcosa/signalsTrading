# deploy_python.ps1
param(
    [string]$TaskName = "PythonApp_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
)

$ScriptPath = Join-Path (Get-Location) "windows.ps1"
$WorkingDirectory = (Get-Location).Path

# Verificar que el script PowerShell existe
if (-not (Test-Path $ScriptPath)) {
    Write-Error "No se encuentra windows.ps1 en el directorio actual"
    exit 1
}

Write-Host "Creando tarea programada: $TaskName"
Write-Host "Script: $ScriptPath"
Write-Host "Directorio: $WorkingDirectory"

# Crear tarea que se ejecutará en 1 minuto
$StartTime = (Get-Date).AddMinutes(1).ToString("HH:mm")
schtasks /create /tn $TaskName /tr "powershell -ExecutionPolicy Bypass -File `"$ScriptPath`"" /sc once /st $StartTime /f /ru "SYSTEM"

# Esperar a que la tarea se cree
Start-Sleep -Seconds 2

# Verificar creación
Write-Host "`nTarea creada exitosamente:"
try {
    schtasks /query /tn $TaskName /fo list | Select-Object -First 10
} catch {
    Write-Host "ERROR: No se pudo verificar la tarea - $($_.Exception.Message)"
}

# Ejecutar inmediatamente
Write-Host "`nEjecutando tarea..."
try {
    schtasks /run /tn $TaskName
    Write-Host "Tarea ejecutada correctamente"
} catch {
    Write-Host "ERROR al ejecutar tarea: $($_.Exception.Message)"
}

# Esperar y verificar
Start-Sleep -Seconds 10
Write-Host "`nVerificando archivos de log..."
if (Test-Path "execution_log.txt") {
    Write-Host "Log principal creado. Últimas líneas:"
    Get-Content "execution_log.txt" -Tail 5
} else {
    Write-Host "INFO: execution_log.txt no creado aún (puede estar en proceso)"
}

if (Test-Path "output_log.txt") {
    Write-Host "Log de output creado. Tamaño: $((Get-Item 'output_log.txt').Length) bytes"
} else {
    Write-Host "INFO: output_log.txt no creado (puede ser normal si no hay output)"
}

Write-Host "`nProcesos Python activos:"
Get-Process python* -ErrorAction SilentlyContinue | Format-Table Id, Name, CPU, Path

Write-Host "`n=== DEPLOYMENT COMPLETADO ==="
Write-Host "Tarea: $TaskName"
Write-Host "Los logs deberían estar en: $WorkingDirectory"
Write-Host "Verificar con: Get-Content execution_log.txt"
Write-Host "Y ver procesos con: Get-Process python*"