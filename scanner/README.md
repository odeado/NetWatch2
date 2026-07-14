# Win NetWatch RMM — Módulo de Escaneo (v1)

Primer bloque del sistema: descubre hosts activos en una subred, detecta
puertos clave de Windows, resuelve hostname y MAC, y calcula un score de
confianza para filtrar falsos positivos (IPs flotantes).

Solo usa librería estándar de Python — no requiere `pip install` de nada.

## Requisitos

- Python 3.8 o superior instalado en tu PC (Windows).
- Ejecutarlo **en tu máquina**, no en un entorno aislado/sandbox: el script
  necesita acceso real a tu red local para poder hacer ping y escanear puertos.

## Uso

Abre PowerShell o CMD en esta carpeta y ejecuta uno de estos comandos:

```
# Sin flags: escanea la subred por defecto (config.json: default_subnet, hoy 172.30.100.0/24)
python scanner.py

# Autodetecta tu subred actual (la del PC donde corres el script) y la escanea
python scanner.py --local

# Escanea una subred específica manualmente
python scanner.py --subnet 192.168.1.0/24

# Escanea las 5 subredes corporativas definidas en config.json
python scanner.py --all

# Ajusta la concurrencia (hilos simultáneos), por defecto 64
python scanner.py --local --workers 128
```

Un host se marca como activo si responde al ping **o** si tiene algún puerto
clave abierto (RDP/SMB/RPC/NetBIOS/WinRM) — muchos firewalls corporativos
bloquean ICMP pero no estos puertos, así que el escaneo de puertos se hace
siempre, sin depender del resultado del ping.

Al terminar, imprime una tabla en consola con IP, hostname, MAC, latencia,
puertos abiertos y nivel de confianza, y además guarda un JSON completo en
`scanner/results/scan_<fecha>.json` con todos los datos crudos (esto es lo
que luego alimentará el backend/base de datos del sistema completo).

## Qué revisa por host

- Ping (pérdida de paquetes y latencia).
- Puertos: 3389 (RDP), 445 (SMB), 135 (RPC), 139 (NetBIOS), 5985/5986 (WinRM).
- Hostname vía DNS inverso.
- MAC vía tabla ARP local (`arp -a` en Windows).
- Score de confianza (0-100) para distinguir equipos reales de IPs de paso.

## Configuración (`config.json`)

Ahí están las 5 subredes corporativas del informe (Antofagasta Rendic x2,
Antofagasta Matta, Arica, Iquique), la lista de puertos, y los timeouts.
Puedes editar este archivo sin tocar el código.

## Monitoreo continuo (`monitor.py`)

`scanner.py` es para escaneos puntuales (uno y listo). Para que el sistema
esté escaneando todo el rato y detecte solo cuando un equipo cae o vuelve,
usa `monitor.py` en su lugar — corre en bucle y escribe directo a la base de
datos de la webapp (no genera JSON ni requiere que lo importes a mano):

```
# Deja esto corriendo en una terminal (Ctrl+C para detener):
python monitor.py                          # subred por defecto, cada 60s
python monitor.py --subnet 172.30.101.0/24 --interval 30
python monitor.py --all --interval 120      # las 5 subredes, cada 2 minutos
python monitor.py --once                    # un solo ciclo, para probar
```

Cada vez que un equipo pasa de online a offline (o viceversa), o aparece uno
nuevo, queda registrado como evento — eso es lo que ves en el panel
"Últimos cambios" de la webapp (con aviso tipo toast y sonido en vivo).

Para evitar falsos positivos de "offline" por un hipo de red o de firewall en
un solo ciclo, un equipo no se marca offline al primer fallo: tiene que fallar
`monitor.offline_after_misses` ciclos seguidos (por defecto 2) antes de
dispararse el evento/aviso. Si notas equipos que se marcan offline y vuelven
online casi de inmediato, puedes subir ese número en `config.json`. Este
cambio requiere reiniciar `monitor.py` para tomar efecto (no se recarga solo).

## Ver los resultados en el navegador

La interfaz web (carpeta `../webapp`) lee la base de datos que llena
`monitor.py` (o el JSON que generas con `scanner.py` + el botón "Importar").
Ahí puedes marcar cada equipo como **confirmado** o **descartado** (falso
positivo), ver quién está en línea ahora mismo, y completar la ficha técnica
de cada uno. Ver `webapp/README.md` para más detalle.

## Próximos pasos (no incluidos aún)

- Sincronización híbrida Firebase/Postgres, y decidir dónde alojar esto
  fuera de tu equipo local.
- Detección de patrones (reinicios frecuentes, desconexiones repetidas).
- Acciones remotas (RDP, WoL, reinicio, scripts PowerShell).
- Roles y permisos.

Este scanner + monitor son la base: una vez validado contra tu red real,
seguimos con el siguiente módulo.
