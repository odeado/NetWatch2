# NetWatch Colector

Pequeno vigilante independiente de NetWatch. Corre en un PC que quede siempre
encendido, lee los escaneos que ya publican los sitios en Firebase
(`escaneos_remotos/*`) y detecta quien cae, quien vuelve y que equipo nuevo
aparece. Publica el resultado en `colector/*` y la app Android (NetWatch
Colector) lo convierte en notificaciones, una por equipo. Los equipos marcados
como criticos en NetWatch llegan con alerta fuerte y vibracion.

No escanea nada por si mismo y no depende del PC donde corre NetWatch.

## 1. Instalar el colector (PC siempre encendido)
1. Copiar la carpeta `colector` al PC (necesita Python 3.8+; `asegurar_python.bat` ayuda).
2. Copiar `colector_config.json.example` a `colector_config.json` y completar
   `email` / `password`. Conviene crear una cuenta propia en Firebase
   (Authentication > Add user), por ejemplo `colector@...`.
3. Ejecutar `Probar Colector.bat`: hace un ciclo de solo lectura y muestra los sitios y equipos.
4. `Instalar_Inicio_Automatico.bat` lo deja corriendo oculto al iniciar Windows.
   `Detener Colector.bat` lo detiene. Log en `colector.log`.

## 2. Reglas de Firebase
La cuenta del colector escribe `colector/*` y lee `escaneos_remotos/*` y `equipos`.
Opcional: crear una segunda cuenta solo lectura para el celular y ponerla en
`telefono` del config; la app la usara en lugar de la del colector.

## 3. App Android
1. En el PC con Android Studio ejecutar `Compilar_APK_NetWatch.bat`.
2. Pasar `NetWatchColector.apk` al celular e instalarlo (permitir origen desconocido).
3. Abrirla: aceptar notificaciones y excluirla de la optimizacion de bateria.
   La primera vez toma el estado actual como base (no notifica el historial).
4. Avisa tambien si el propio colector deja de reportar por mas de 5 minutos.

## Que muestra la app
Igual que el panel: chips de agentes de escaneo (Activo / Retrasado / Sin informacion, con el
mismo criterio de tiempos), gauges de % fuera de linea por red (verde < 20 %, ambar >= 20 %,
rojo >= 50 %), prioritarios y ultimos cambios. Las notificaciones usan los mismos textos
("Paso a offline", "Volvio online", "Nuevo equipo"); si 3 o mas equipos de un mismo sitio
cambian a la vez sale un aviso agrupado del sitio, y los prioritarios avisan ademas solos.
El nombre de cada red sale de sitio + 3er octeto del CIDR ("Iquique 112"); se puede forzar con
`etiquetas` en el config.

## Notas
- La app consulta Firebase cada 60 s (aprox. unos pocos KB por consulta).
- Un equipo se da por caido tras 2 escaneos seguidos sin respuesta (`offline_tras_escaneos`).
- El `.apk` y los `.bat` no se probaron aun en un Windows/Android real: si algo falla, avisar con el mensaje exacto.
