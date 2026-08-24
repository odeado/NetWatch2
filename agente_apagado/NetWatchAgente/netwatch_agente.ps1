# netwatch_agente.ps1
#
# Agente de apagado/reinicio remoto para Win NetWatch RMM.
#
# QUE HACE: corre como tarea programada en CADA PC (no en el servidor).
# Cada vez que se ejecuta, pregunta a Firebase si hay una orden de
# apagar/reiniciar pendiente para la(s) IP(s) de esta PC y, si la hay, la
# ejecuta localmente con "shutdown.exe" y borra la orden.
#
# POR QUE ASI Y NO COMO ANTES: el metodo viejo (net use + shutdown /m desde
# el servidor) requeria que el servidor se conectara HACIA cada PC por el
# puerto 445 (SMB) -- eso lo bloqueaba ESET Endpoint Security sin que Andres
# tuviera acceso a esa consola para hacer una excepcion (ver conversacion
# 2026-08-18). Este agente da vuelta la direccion: es la PC la que pregunta
# hacia afuera (HTTPS saliente, puerto 443), que casi ningun antivirus
# bloquea por defecto.
#
# COMO SE INSTALA: ver README_agente.md en esta misma carpeta -- resumen:
# Rendic/Rendic2 no tienen dominio/Active Directory, asi que se instala PC
# por PC (por RDP): se copia esta carpeta a la PC y se corre
# instalar_agente.bat como administrador, que crea una tarea programada
# que ejecuta este script cada 1 minuto, como SYSTEM, sin necesitar que
# nadie este logueado.
#
# REQUISITOS: Windows con PowerShell 2.0 o superior (viene con Windows 7 SP1
# en adelante) -- por eso este script usa [System.Net.WebClient] en vez de
# Invoke-RestMethod/Invoke-WebRequest (esos cmdlets no existen en PowerShell
# 2.0, que es lo que trae Windows 7 de fabrica).
#
# NOTA IMPORTANTE (compatibilidad JSON): ConvertFrom-Json/ConvertTo-Json
# TAMPOCO existen en PowerShell 2.0 (llegaron recien en PS 3.0 / WMF 3.0).
# Por eso este script NO los usa directamente: en su lugar define
# ConvertFrom-Json2 / ConvertTo-Json2 mas abajo, que hacen lo mismo usando
# System.Web.Extensions (JavaScriptSerializer), disponible desde .NET
# Framework 3.5, que ya viene instalado en Windows 7 SP1.

$CarpetaScript = Split-Path -Parent $MyInvocation.MyCommand.Path
$RutaConfig = Join-Path $CarpetaScript "agente_config.json"
$RutaLog = Join-Path $CarpetaScript "netwatch_agente.log"

function Escribir-Log {
    param([string]$Mensaje)
    $linea = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Mensaje"
    try { Add-Content -Path $RutaLog -Value $linea -ErrorAction SilentlyContinue } catch {}
}

# TLS 1.2: el enum [Net.SecurityProtocolType]::Tls12 solo existe si esta PC
# tiene .NET Framework 4.5+ instalado. En PCs con solo .NET 3.5 (Windows 7
# "de fabrica") esta linea truena. Usamos el valor numerico (3072 = Tls12)
# para no depender del nombre del enum, pero OJO: esto NO alcanza si el
# .NET instalado en verdad no soporta TLS 1.2 a nivel de protocolo -- en
# ese caso hay que instalar .NET Framework 4.7.2+ en la PC (una vez, no
# rompe nada, corre en paralelo con el 3.5 que ya tiene).
try {
    [Net.ServicePointManager]::SecurityProtocol = 3072
} catch {
    Escribir-Log "ERROR: no se pudo activar TLS 1.2 ($($_.Exception.Message)). Esta PC necesita .NET Framework 4.7.2 o superior instalado (Firebase exige TLS 1.2). Instalalo y reinicia la PC."
    exit 1
}

# --- Capa de compatibilidad JSON para PowerShell 2.0 -----------------------
try {
    Add-Type -AssemblyName System.Web.Extensions -ErrorAction Stop
} catch {
    Escribir-Log "ERROR: no se pudo cargar System.Web.Extensions (JSON): $($_.Exception.Message)"
    exit 1
}
$script:JsSerializer = New-Object System.Web.Script.Serialization.JavaScriptSerializer
$script:JsSerializer.MaxJsonLength = 67108864

function ConvertFrom-Json2 {
    param([Parameter(ValueFromPipeline=$true)][string]$InputObject)
    process {
        # DeserializeObject devuelve Hashtable/ArrayList; los envolvemos en
        # PSObject para poder seguir usando $x.propiedad como en el resto
        # del script (igual que con ConvertFrom-Json nativo).
        $obj = $script:JsSerializer.DeserializeObject($InputObject)
        return (ConvertTo-PSObjectRecursivo $obj)
    }
}

function ConvertTo-PSObjectRecursivo {
    param($Valor)
    if ($Valor -is [System.Collections.IDictionary]) {
        $props = @{}
        foreach ($clave in $Valor.Keys) {
            $props[$clave] = ConvertTo-PSObjectRecursivo $Valor[$clave]
        }
        return New-Object PSObject -Property $props
    } elseif ($Valor -is [System.Collections.IEnumerable] -and -not ($Valor -is [string])) {
        $lista = @()
        foreach ($item in $Valor) { $lista += ,(ConvertTo-PSObjectRecursivo $item) }
        return ,$lista
    } else {
        return $Valor
    }
}

function ConvertTo-Json2 {
    param([Parameter(ValueFromPipeline=$true)]$InputObject)
    process {
        return $script:JsSerializer.Serialize($InputObject)
    }
}
# ---------------------------------------------------------------------------

if (-not (Test-Path $RutaConfig)) {
    Escribir-Log "ERROR: no se encontro $RutaConfig -- copia agente_config.json.example y completa los datos."
    exit 1
}

try {
    $cfg = Get-Content $RutaConfig -Raw | ConvertFrom-Json2
} catch {
    Escribir-Log "ERROR: agente_config.json no es JSON valido: $($_.Exception.Message)"
    exit 1
}

foreach ($campo in @("databaseURL", "apiKey", "email", "password")) {
    if (-not $cfg.$campo) {
        Escribir-Log "ERROR: falta el campo '$campo' en agente_config.json"
        exit 1
    }
}
$databaseURL = $cfg.databaseURL.TrimEnd("/")

function Llamar-Http {
    param([string]$Url, [string]$Metodo, [string]$Cuerpo)
    $wc = New-Object System.Net.WebClient
    $wc.Headers["Content-Type"] = "application/json"
    try {
        if ($Metodo -eq "GET") {
            return $wc.DownloadString($Url)
        } else {
            if ($null -eq $Cuerpo) { $Cuerpo = "" }
            return $wc.UploadString($Url, $Metodo, $Cuerpo)
        }
    } catch [System.Net.WebException] {
        # Firebase manda el detalle util (ej. "Permission denied") en el
        # CUERPO de la respuesta de error, que .NET no incluye en
        # $_.Exception.Message por defecto -- lo leemos aparte para que
        # quede en el log y sepamos exactamente que regla/permiso falla.
        $cuerpoError = $null
        try {
            $streamError = $_.Exception.Response.GetResponseStream()
            $lector = New-Object System.IO.StreamReader($streamError)
            $cuerpoError = $lector.ReadToEnd()
            $lector.Close()
        } catch {}
        if ($cuerpoError) {
            throw "$($_.Exception.Message) -- respuesta de Firebase: $cuerpoError"
        } else {
            throw
        }
    } finally {
        $wc.Dispose()
    }
}

# --- 1. Autenticarse contra Firebase (mismo mecanismo que usa el servidor
# en webapp/firebase_sync.py) para obtener un idToken valido por 1 hora. ---
try {
    $urlLogin = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=$($cfg.apiKey)"
    $cuerpoLogin = (@{ email = $cfg.email; password = $cfg.password; returnSecureToken = $true } | ConvertTo-Json2)
    $respLogin = Llamar-Http -Url $urlLogin -Metodo "POST" -Cuerpo $cuerpoLogin
    $idToken = ($respLogin | ConvertFrom-Json2).idToken
    if (-not $idToken) { throw "respuesta sin idToken" }
} catch {
    Escribir-Log "ERROR al autenticar contra Firebase: $($_.Exception.Message)"
    exit 1
}

# --- 2. IPs locales (se prueban todas las que tenga esta PC, por si tiene
# mas de una tarjeta de red -- WMI en vez de Get-NetIPAddress porque este
# ultimo no existe en PowerShell 2.0/Windows 7). ---
$ipsLocales = @()
try {
    $adaptadores = Get-WmiObject Win32_NetworkAdapterConfiguration -Filter "IPEnabled = TRUE"
    foreach ($a in $adaptadores) {
        foreach ($ip in $a.IPAddress) {
            if ($ip -match '^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$') { $ipsLocales += $ip }
        }
    }
} catch {
    Escribir-Log "ERROR leyendo IPs locales: $($_.Exception.Message)"
    exit 1
}

if ($ipsLocales.Count -eq 0) {
    Escribir-Log "Sin IPs IPv4 locales detectadas -- nada que revisar esta vez."
    exit 0
}

# --- 3. Por cada IP local, preguntar si hay una orden pendiente. ---
foreach ($ip in $ipsLocales) {
    $claveIp = $ip -replace '\.', '_'
    $urlComando = "$databaseURL/comandos_equipo/$claveIp.json?auth=$idToken"
    try {
        $crudo = Llamar-Http -Url $urlComando -Metodo "GET"
    } catch {
        Escribir-Log "ERROR consultando comando para $ip : $($_.Exception.Message)"
        continue
    }
    if (-not $crudo -or $crudo -eq "null") { continue }

    try {
        $orden = $crudo | ConvertFrom-Json2
    } catch {
        Escribir-Log "ERROR: orden para $ip no es JSON valido, se ignora: $crudo"
        continue
    }

    $accion = $orden.accion
    $avisoSegundos = if ($orden.aviso_segundos) { [int]$orden.aviso_segundos } else { 60 }
    $mensaje = if ($orden.mensaje) { $orden.mensaje } else { "Apagado programado desde NetWatch - Sistemas" }

    $bandera = $null
    if ($accion -eq "apagar") { $bandera = "/s" }
    elseif ($accion -eq "reiniciar") { $bandera = "/r" }

    if (-not $bandera) {
        Escribir-Log "Orden para $ip con accion desconocida ('$accion'), se ignora y se borra."
        try { Llamar-Http -Url $urlComando -Metodo "DELETE" -Cuerpo "" | Out-Null } catch {}
        continue
    }

    Escribir-Log "Orden encontrada para $ip : $accion en $avisoSegundos s. Ejecutando shutdown.exe..."
    try {
        & shutdown.exe $bandera /t $avisoSegundos /c $mensaje
        Escribir-Log "shutdown.exe ejecutado OK para $ip ($accion)."
    } catch {
        Escribir-Log "ERROR ejecutando shutdown.exe: $($_.Exception.Message)"
    }

    # Borrar la orden SIEMPRE despues de intentar ejecutarla -- si no, la
    # proxima vez que corra el agente (1 minuto despues) la va a volver a
    # ejecutar, y el usuario terminaria con un reinicio en loop cada minuto.
    try {
        Llamar-Http -Url $urlComando -Metodo "DELETE" -Cuerpo "" | Out-Null
    } catch {
        Escribir-Log "ERROR borrando la orden ya ejecutada para $ip : $($_.Exception.Message)"
    }
}

# --- 4. "Heartbeat" opcional: avisa que este agente esta vivo y a que hora
# paso por ultima vez, por IP. No hace nada por si solo hoy, pero deja la
# base para que mas adelante NetWatch pueda mostrar "agente instalado / no
# instalado" por equipo en vez de tener que asumirlo. ---
foreach ($ip in $ipsLocales) {
    $claveIp = $ip -replace '\.', '_'
    $urlEstado = "$databaseURL/agentes_estado/$claveIp.json?auth=$idToken"
    $cuerpoEstado = (@{
        ip = $ip
        hostname = $env:COMPUTERNAME
        visto_en = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    } | ConvertTo-Json2)
    try { Llamar-Http -Url $urlEstado -Metodo "PUT" -Cuerpo $cuerpoEstado | Out-Null } catch {}
}

exit 0