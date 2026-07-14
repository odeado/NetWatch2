<#
Win NetWatch RMM - Manejador del protocolo netwatchrdp://
============================================================
Recibe la URL completa que Windows pasa al abrir un enlace netwatchrdp://IP
y lanza el cliente nativo de Escritorio Remoto (mstsc.exe) apuntando a esa IP.

No lo ejecutes a mano: lo invoca Windows automaticamente cuando haces clic
en un boton "Conectar por RDP" de la webapp, despues de instalar el
protocolo con instalar_protocolo_rdp.bat.
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$Url
)

# Ejemplo de entrada: netwatchrdp://172.30.100.17  o  netwatchrdp://172.30.100.17/
$target = $Url -replace '^netwatchrdp://', ''
$target = $target.TrimEnd('/')

if ([string]::IsNullOrWhiteSpace($target)) {
    exit
}

Start-Process "mstsc.exe" -ArgumentList "/v:$target"
