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
