# PowerShell script para monitorear cuando el scanner vuelve a estar activo
$logFile = "$PSScriptRoot\scanner\monitor_error.log"
$lastLine = ""
$checkInterval = 10  # segundos

Write-Host "Monitoreando scanner... (Ctrl+C para detener)"
Write-Host "Revisa cuando hay nuevas líneas en el log"
Write-Host "Archivo: $logFile"
Write-Host ""

while ($true) {
    try {
        if (Test-Path $logFile) {
            $content = @(Get-Content $logFile -Tail 1 -ErrorAction SilentlyContinue)
            $currentLine = $content -join ""
            
            if ($currentLine -and $currentLine -ne $lastLine) {
                $timestamp = (Get-ChildItem $logFile).LastWriteTime
                Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Nueva actividad detectada!" -ForegroundColor Green
                Write-Host "  $currentLine" -ForegroundColor Cyan
                Write-Host ""
                $lastLine = $currentLine
            }
        }
    } catch {
        # Ignorar errores de lectura
    }
    
    Start-Sleep -Seconds $checkInterval
}
