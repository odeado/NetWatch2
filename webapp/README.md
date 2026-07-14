# Win NetWatch RMM — Web + Inventario + Tickets + RDP + Administracion + Topologia (v8)

Los equipos viven en una base de datos local (`netwatch.db`, SQLite — un solo
archivo, sin instalar ningún servidor de base de datos aparte). Se llena de
dos formas:

- **Recomendado**: dejando `python monitor.py` corriendo (en `../scanner/`)
  — escanea en bucle y escribe directo aquí, sin pasos manuales.
- **Puntual**: corriendo `python scanner.py` una vez y usando el botón
  **"Importar"** de la webapp.

Ambos caminos comparten la misma lógica, así que puedes combinarlos sin
problema (por ejemplo: dejar el monitor corriendo, y de vez en cuando un
`scanner.py --all` para revisar las otras subredes).

Desde la web puedes:

- Ver todos los equipos, con un punto **verde/rojo** que indica si están
  en línea ahora mismo o no (un punto **azul** indica que fue agregado a mano,
  sin monitoreo automático — ver más abajo).
- Ver el panel **"Últimos cambios"**: quién pasó a offline, quién volvió
  online, y los equipos nuevos — la página se refresca sola cada 20s, así
  que no necesitas recargar a mano.
- Filtrar por pendiente/confirmado/descartado, y confirmar/descartar cada equipo.
- Abrir la **ficha** de cada equipo (clic en la IP) y completar sus datos
  administrativos y de hardware: marca, modelo, número de serie, responsable,
  sucursal, garantía, CPU/RAM/almacenamiento, estado del ciclo de vida
  (activo/en reparación/retirado/bodega), si es crítico o está gestionado, y notas.
- Si el escaneo no logró detectar el **Hostname** o la **MAC** (pasa seguido
  cuando el equipo está en otra VLAN o te conectas por VPN), puedes escribirlos
  a mano en la ficha, en la sección "Identificación". Si un escaneo posterior
  sí los detecta, el valor real pisa al que escribiste a mano automáticamente.
- **Administración** (arriba a la derecha, junto a Tickets): un directorio de
  responsables (nombre, correo, cargo, sucursal, teléfono). Desde ahí agregas
  personas una sola vez, y luego en la ficha de cada equipo eliges quién es
  el responsable desde un desplegable, en vez de escribirlo a mano cada vez.
  Puedes desactivar a alguien que ya no corresponda sin perder el historial
  de qué equipos tuvo asignados.
- **Topología** (arriba a la derecha): registro de dispositivos de red
  (switch/router/Fortinet/otro) con marca, modelo, número de serie, IP,
  sucursal y ubicación. Puedes elegir una **plantilla de puertos real**
  (switch Cisco 24 o 48 + 2 SFP, Firewall Fortinet FG-60F, conversor de
  medios fibra/cobre/consola) para que el mapa se vea igual al equipo
  físico, o usar el modo "Genérico" definiendo cuántas **bocas de cobre**
  y cuántas **bocas de fibra** tiene (por ejemplo, un Juniper con 4 bocas
  de fibra). El botón **"Editar puertos"** de cada fila abre una ventana
  con el mapa visual: haces clic en una boca y eliges qué conectar ahí,
  ya sea un **equipo** del inventario o **otro dispositivo de red**
  (switch-switch, Fortinet-switch, etc.) — sin SNMP, 100% manual. Si
  prefieres, también puedes asignar el dispositivo y escribir el puerto
  a mano desde la ficha del equipo (útil para
  nomenclaturas tipo "Gi1/0/24"); esos casos aparecen listados aparte
  en Topología si no calzan con la numeración de bocas definida.
- **Agregar equipo manualmente** (desplegable arriba del inventario): para
  equipos que no siempre están en el rango que escanea el monitor — por
  ejemplo notebooks que se conectan por VPN o desde afuera de la red de la
  empresa. Escribes una IP o un identificador libre (si no tiene IP fija de
  la empresa), y queda en el inventario igual que el resto, pero con el punto
  azul de "sin monitoreo automático" en vez de en línea/fuera de línea. Desde
  ahí te lleva a la ficha para completar el resto de los datos.
- **Exportar CSV** (arriba a la derecha): descarga el inventario completo
  (ficha, estado, responsable, dispositivo/puerto, tickets abiertos) como
  un archivo `.csv` listo para abrir en Excel (con acentos correctos).
- Abrir y dar seguimiento a **tickets de soporte** por equipo (prioridad,
  asignado a, estado abierto/en progreso/resuelto), y ver todos los tickets
  de todos los equipos en el panel global **"Tickets de soporte"** (arriba
  a la derecha), útil para el triage diario.
- **Conectarte por RDP en un clic**: en cualquier equipo con el puerto 3389
  detectado, aparece un botón "RDP" (en la lista) o "Conectar por RDP" (en
  la ficha). Con el protocolo instalado (ver más abajo) esto abre
  Escritorio Remoto directo, sin descargar nada. Cada conexión queda
  registrada en el historial de la ficha (desde qué IP y cuándo).

Si venías de una versión anterior con `confirmations.json`, esas marcas se
migran solas la primera vez que importas o corres el monitor.

## Requisitos

- Python 3.8+ (el mismo que usa el scanner).
- Flask: `pip install -r requirements.txt` (una sola vez).
- Para que el botón RDP abra Escritorio Remoto directo (recomendado): corre
  **una sola vez**, con doble clic, `..\tools\instalar_protocolo_rdp.bat`.
  No requiere permisos de administrador. La primera vez que uses el botón
  RDP, el navegador va a preguntar si quieres abrir "NetWatch RDP Protocol"
  — acepta esa ventana (y marca "recordar mi elección" si aparece).
  Si no lo instalas, el botón "Descargar .rdp en su lugar" de la página de
  conexión sigue funcionando igual que antes (descarga el archivo y lo abres tú).

## Uso

**Con un clic**: doble clic en `Iniciar NetWatch.bat` (en la carpeta raiz del
proyecto, un nivel arriba de `webapp/`). Abre el monitor y la pagina web cada
uno en su ventana, y despues abre el navegador solo en `localhost:5001`. Para
apagar todo, cierra esas dos ventanas.

**Manual** (si prefieres controlarlo tu mismo):

**Paso 1** — deja el monitor corriendo (en una terminal, desde `scanner/`):

```
python monitor.py
```

**Paso 2** — en otra terminal, desde esta carpeta (`webapp/`):

```
python app.py
```

Abre **http://localhost:5001** (o el puerto que hayas configurado en
`app.py` si el 5001 también estaba ocupado). Si dejaste el monitor
corriendo, la tabla y el panel de cambios se van a ir llenando y
actualizando solos, sin que tengas que hacer nada más.

## Archivos

- `db.py` — esquema y funciones de acceso a la base de datos (tablas
  `equipos`, `eventos`, `tickets` y `rdp_history`), incluida la lógica que
  detecta cuándo un equipo cambia de en línea a fuera de línea.
- `app.py` — rutas Flask (listado, importar, confirmar, ficha, tickets, descarga de `.rdp`).
- `netwatch.db` — se crea solo la primera vez que corres `app.py` o
  `monitor.py`. Este es tu inventario real: consérvalo (no lo borres) al
  mover la carpeta a otro equipo.

## Próximos pasos

- Backend con API propia y posible sincronización a la nube (Firebase/Postgres),
  cuando decidamos dónde alojar esto fuera de tu equipo local.
- Detección de patrones más fina (reinicios frecuentes, desconexiones
  repetidas en poco tiempo) a partir del historial de la tabla `eventos`.
- Topología / mapeo de puertos de switch.
- Acciones remotas adicionales (Wake-on-LAN, reinicio/apagado remoto,
  scripts PowerShell), y roles y permisos — según el informe del proyecto.
