# revisar_puerto.ps1 -- usado por "Cambiar Puerto.bat".
# Dice si un puerto TCP ya esta en uso en esta PC, y por quien.
#   Codigo de salida 0 = libre
#                    1 = lo usa NetWatch mismo (app.py)
#                    2 = lo usa otro programa (se imprime nombre y PID)
# No depende del idioma de Windows (netstat escribe "LISTENING" o
# "ESCUCHANDO" segun el idioma): un puerto en escucha es el que tiene
# direccion remota 0.0.0.0:0 o [::]:0.
param([int]$Puerto)

$duenoPid = $null
foreach ($linea in (netstat -ano)) {
    if ($linea -match "^\s*TCP\s+\S+:$Puerto\s+(0\.0\.0\.0:0|\[::\]:0)\s+\S+\s+(\d+)\s*$") {
        $duenoPid = [int]$matches[2]
        break
    }
}

if ($duenoPid -eq $null) { exit 0 }

$proceso = Get-WmiObject Win32_Process -Filter "ProcessId=$duenoPid"
$nombre = "desconocido"
$comando = ""
if ($proceso) { $nombre = $proceso.Name; $comando = [string]$proceso.CommandLine }

if ($comando -like "*app.py*") { exit 1 }

Write-Host "El puerto $Puerto ya esta en uso por: $nombre (PID $duenoPid)"
if ($comando) { Write-Host "  $comando" }
exit 2
