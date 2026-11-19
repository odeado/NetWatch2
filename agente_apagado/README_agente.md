# Agente de apagado/reinicio remoto -- instalacion

Este agente reemplaza el metodo viejo (conexion directa del servidor hacia
cada PC por SMB), que quedo bloqueado por ESET Endpoint Security sin que
hubiera forma de hacer una excepcion sin acceso a esa consola. Ahora es
cada PC la que pregunta hacia afuera si tiene una orden pendiente -- trafico
saliente por HTTPS, que ESET deja pasar sin problema.

Son 3 pasos: crear una cuenta de Firebase solo para el agente, completar la
configuracion, y desplegar la tarea programada en las PCs por GPO.

## Paso 1 -- Crear una cuenta de Firebase solo para el agente

No reutilices el email/password que ya usa `webapp/firebase_config.json`
(esa cuenta tiene acceso total a los datos de empleados/equipos). El
agente se va a copiar a cientos de PCs, asi que conviene que tenga sus
propias credenciales, faciles de revocar sin afectar el resto del sistema.

1. Entra a la consola de Firebase de tu proyecto -> Authentication -> Users.
2. Click "Add user". Usa un correo cualquiera que no necesite existir de
   verdad (ej. `agente-apagado@rendic.cl`) y una contrasena nueva.
3. Anota esas credenciales, las vas a necesitar en el Paso 2.

(Opcional, mas seguro pero no obligatorio para que esto funcione hoy:
restringir las reglas de la base de datos para que esta cuenta especifica
solo pueda leer/escribir en `comandos_equipo` y `agentes_estado`, no en
`empleados`/`equipos`/etc. Si quieres esto, avisame y te armo las reglas
exactas -- necesito ver las reglas actuales primero.)

## Paso 2 -- Completar la configuracion

En esta carpeta (`agente_apagado/`):

1. Copia `agente_config.json.example` como `agente_config.json`.
2. Completa:
   - `databaseURL` y `apiKey`: son los MISMOS valores que ya tienes en
     `webapp/firebase_config.json` (esos dos campos no son secretos, se
     pueden compartir).
   - `email` y `password`: los que creaste en el Paso 1.
3. `agente_config.json` (el real, con contrasena) NO se sube a git -- ya
   quedo agregado a `.gitignore`.

## Paso 3 -- Instalar en cada PC (sin dominio/Active Directory, a mano)

Como Rendic/Rendic2 no usan un dominio, no hay GPO para empujar esto solo.
Se instala PC por PC usando el mismo acceso RDP que ya usas con la cuenta
`sistemas`. Toma 1-2 minutos por equipo:

1. Entra por RDP a la PC (igual que siempre).
2. Copia toda la carpeta `agente_apagado/` (ya con `agente_config.json`
   completado -- el MISMO archivo sirve para todas las PCs, no hay que
   editarlo de nuevo en cada una) a una ruta local, por ejemplo:
   `C:\NetWatchAgente\`
   (podes copiarla arrastrando por la ventana de RDP si tienes habilitada
   la redireccion de unidades, o por una carpeta compartida de red, o
   simplemente pegandola si RDP te deja copiar/pegar archivos).
3. En esa PC, click derecho sobre `instalar_agente.bat` -> **"Ejecutar
   como administrador"**.
4. Deberia decir "Listo. La tarea... quedo creada". Si dice que hay que
   correrlo como administrador, repite el paso 3 (asegurate de elegir
   "Ejecutar como administrador", no solo doble click).

Repite esto en cada PC que quieras que pueda apagarse/reiniciarse desde
NetWatch. No hace falta reiniciar la PC para que empiece a funcionar --
la tarea arranca sola apenas se crea.

### Para acelerar el copiado a muchas PCs

Si tienes una carpeta compartida en la red interna (aunque no haya
dominio, un simple recurso compartido de Windows sirve), deja la carpeta
`agente_apagado/` ahi UNA vez y en cada PC solo necesitas entrar por RDP y
correr `instalar_agente.bat` apuntando a esa ruta de red en vez de copiarla
local -- edita la linea `SCRIPT=` de `instalar_agente.bat` si preferis esto,
o avisame y te dejo una version que lea directo desde la red.

## Probar que funciona

1. Espera a que la tarea programada se haya creado en la PC de prueba
   (revisa el Programador de tareas de Windows -- deberia aparecer
   "NetWatch Agente Apagado").
2. Desde NetWatch, en Administracion -> Inventario de Equipos, selecciona
   esa PC y usa **"Reiniciar seleccionados"** (no "Apagar", para poder
   probar sin arriesgar dejarla apagada).
3. Dentro de 1 minuto la PC deberia mostrar el aviso de Windows con la
   cuenta regresiva y reiniciarse sola.
4. Si no pasa nada, revisa `agente_apagado\netwatch_agente.log` (se crea
   en la misma carpeta compartida, con el detalle de cada corrida) o el
   Programador de tareas -> pestana Historial de esa tarea, para ver el
   error exacto (config faltante, error de autenticacion contra Firebase,
   etc).

## Checklist de diagnostico (comandos, por PC, en orden)

Corre esto por RDP en la PC de prueba, desde una consola (CMD o
PowerShell) **como administrador**, parado en la carpeta del agente
(ej. `cd C:\NetWatchAgente`):

1. **La tarea existe y como le fue la ultima vez:**
   ```
   schtasks /query /tn "NetWatch Agente Apagado" /v /fo list
   ```
   Mira "Last Run Time" y "Last Result" (0 = OK, cualquier otro numero =
   fallo esa corrida).

2. **Forzarla a correr ahora** (no esperar el minuto):
   ```
   schtasks /run /tn "NetWatch Agente Apagado"
   ```

3. **Ver el log** (aca queda el detalle real de cada corrida):
   ```
   type netwatch_agente.log
   ```

4. **Correr el script a mano** (el mas util si el log no dice nada o si
   `schtasks`/`shutdown` no se reconocen al tipearlos sueltos -- pasa en
   PCs donde el PATH del sistema no incluye `System32`, ver mas abajo):
   ```
   powershell -ExecutionPolicy Bypass -File "netwatch_agente.ps1"
   ```

### Errores conocidos y que significan

- **`no se pudo activar TLS 1.2` / `"3072" al tipo "SecurityProtocolType"`**
  -- la PC no tiene .NET Framework 4.5+ instalado (Windows 7 "de fabrica"
  solo trae 3.5). Instala `ndp472-kb4054531-web.exe` (incluido en esta
  carpeta) y reinicia la PC. Si tambien falla `Add-Type -AssemblyName
  System.Web.Extensions`, instala antes el parche `Win7...KB3191566...`
  (tambien incluido) -- ese es el que trae TLS 1.2 a nivel de sistema
  operativo, no solo a nivel de .NET.

- **`'schtasks' / 'shutdown.exe' is not recognized...`** al escribirlos
  sueltos en una consola -- el PATH de esa PC no incluye `System32`
  (raro, pero pasa en imagenes de Windows modificadas). No afecta al
  agente en si (desde 2026-08-25 llama a `shutdown.exe` por su ruta
  completa), pero si te pasa a ti al probar algo a mano, usa la ruta
  completa: `C:\Windows\System32\schtasks.exe`.

- **`QUOTA_EXCEEDED : Exceeded quota for verifying passwords`** -- bug
  real encontrado el 2026-08-25: versiones viejas del agente pedian
  clave a Firebase EN CADA CORRIDA (cada 1 min), y eso agota la cuota de
  login de Firebase con pocas PCs. Arreglado desde esa fecha: el agente
  ahora guarda un `agente_token_cache.json` junto al script y solo pide
  clave de nuevo si ese cache no existe o vencio. Si ves este error,
  asegurate de tener la version mas nueva de `netwatch_agente.ps1`
  (busca la funcion `Guardar-Cache` en el archivo -- si no esta, es una
  version vieja, reemplazala).

## Version 1.1.0 (2026-11-19) -- que cambio y como actualizar

Cambios del agente (`netwatch_agente.ps1`):

- **Las ordenes caducan.** El agente descarta (y borra) una orden con mas de
  10 min de antiguedad, medida con la hora del servidor de Firebase (no con
  el reloj de la PC). Antes, una orden encolada con la PC apagada se
  ejecutaba apenas alguien la prendia. Ademas el servidor borra de la cola
  lo que nadie recogio en 2 min.
- **Anti-bucle.** Cada orden lleva un `id`; el agente anota el ultimo id
  ejecutado en `agente_ultima_orden.txt` (junto al script) y no vuelve a
  ejecutar el mismo. Protege contra el reinicio en bucle si falla el
  `DELETE` justo despues de ejecutar.
- **Resultado real.** Ahora se revisa el codigo de salida de `shutdown.exe`
  (antes el log decia "OK" aunque fallara) y el resultado queda en
  `agentes_estado/{ip}/ultima_orden` (`ok` / `error` / `caducada` /
  `ignorada`, con codigo y detalle). El panel lo usa para confirmar y
  muestra las PCs donde fallo.
- **Valida lo que viene de Firebase:** aviso entre 10 y 900 s, mensaje de
  hasta 400 caracteres.
- **Heartbeat** (ahora PATCH, para no pisar `ultima_orden`) suma
  `arranque_en` (hora de arranque de Windows), `visto_en_srv` (hora del
  servidor) y `agente_version`. El panel detecta "ya volvio" porque
  `arranque_en` cambio, sin depender del reloj de la PC ni del scanner.

Compatibilidad: el servidor sigue funcionando con agentes viejos (no
reportan resultado ni arranque; ahi se usa la senal anterior). Por eso se
puede actualizar PC por PC sin apuro.

**Actualizar una PC que ya tiene el agente:** reemplaza solo
`netwatch_agente.ps1` en su carpeta (por RDP, igual que al instalar). No
hace falta volver a correr `instalar_agente.bat` ni tocar
`agente_config.json`: la tarea programada usa el archivo nuevo en la
siguiente corrida. Para confirmar, la linea del heartbeat en
`netwatch_agente.log` dice `agente v1.1.0`.

Nota: `agente_apagado/netwatch_agente.ps1` y
`agente_apagado/NetWatchAgente/netwatch_agente.ps1` son ahora identicos (la
copia de la raiz estaba desactualizada y es la que usa
`instalar_agente.bat`). Si cambias una, copia la otra.
