#!/usr/bin/env python3
"""
Win NetWatch RMM - Monitor continuo
======================================
Escanea la subred en un bucle (por defecto cada 60s) y escribe directo a la
base de datos de la webapp (webapp/netwatch.db) - sin pasos manuales de
importacion. Detecta cuando un equipo pasa de online a offline (o viceversa)
y lo deja registrado para verlo en la web (panel "Ultimos cambios").

Dejalo corriendo en una terminal (o como tarea programada) mientras
trabajas: la webapp se va a ir actualizando sola.

Uso:
    python monitor.py                          Monitorea la subred por defecto, cada 60s
    python monitor.py --subnet 172.30.101.0/24 --interval 30
    python monitor.py --all --interval 120
    python monitor.py --once                    Un solo ciclo y termina (para probar)
"""

import argparse
import ipaddress
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "webapp"))

import scanner  # noqa: E402  (scanner.py del mismo folder)
import db       # noqa: E402  (db.py de ../webapp)
import whatsapp_alertas  # noqa: E402  (mismo folder)
import firebase_sync  # noqa: E402  (de ../webapp, para leer sitios remotos sin VPN)

# Subredes donde el PC central esta FISICAMENTE (misma LAN que el servidor,
# no necesita VPN) -- coincide a proposito con apagado_remoto.REDES_LOCALES.
# Para estas SI tiene sentido un escaneo directo de respaldo si Firebase
# viene con datos viejos (ver _sincronizar_sitios_remotos). Para el resto
# (Matta/Arica/Iquique, que dependen de VPN manual que "se escapa" segun
# Andres) NO tiene sentido: ver bug del 2026-08-21 mas abajo.
_REDES_MISMA_LAN_SERVIDOR = [
    ipaddress.ip_network("172.30.100.0/24"),
    ipaddress.ip_network("172.30.101.0/24"),
]


def _en_misma_lan_servidor(cidr):
    try:
        red = ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return False
    return any(red == local or red.subnet_of(local) for local in _REDES_MISMA_LAN_SERVIDOR)

ETIQUETAS = {"online": "VOLVIO ONLINE", "offline": "PASO A OFFLINE", "nuevo": "NUEVO EQUIPO"}

# La webapp lee este archivo para mostrar la consola del monitor embebida en
# la pagina (en vez de tener que mirar la ventana negra aparte). Se recorta
# solo cuando crece mucho, para no dejarlo crecer para siempre en una maquina
# que queda prendida semanas.
LOG_FILE = Path(__file__).resolve().parent.parent / "webapp" / "monitor.log"
MONITOR_ERROR_LOG = Path(__file__).resolve().parent / "monitor_error.log"
WEB_ERROR_LOG = Path(__file__).resolve().parent.parent / "webapp" / "web_error.log"
LOG_MAX_LINEAS = 2000
LOG_RECORTE_A = 1000


def log(msg):
    print(msg)
    try:
        linea = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(linea)
        _recortar_log_si_crecio_mucho()
    except OSError:
        pass  # si no se puede escribir el log no debe frenar el monitoreo


def _recortar_archivo(ruta, max_lineas=LOG_MAX_LINEAS, recorte_a=LOG_RECORTE_A):
    try:
        p = Path(ruta)
        if not p.exists() or p.stat().st_size < 200_000:
            return
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            lineas = f.readlines()
        if len(lineas) > max_lineas:
            with open(p, "w", encoding="utf-8") as f:
                f.writelines(lineas[-recorte_a:])
    except OSError:
        pass


def _recortar_log_si_crecio_mucho():
    _recortar_archivo(LOG_FILE)
    _recortar_archivo(MONITOR_ERROR_LOG)
    _recortar_archivo(WEB_ERROR_LOG)


def run_once(subnets, config, max_workers):
    offline_after_misses = config.get("monitor", {}).get("offline_after_misses", 2)
    total_eventos = []
    hora = datetime.now().strftime("%H:%M:%S")
    if not subnets:
        # Puede quedar vacia si TODOS los sitios se alimentan por Firebase
        # (ver _sincronizar_sitios_remotos) y no hay ninguna subred que este
        # PC pueda escanear directo -- igual se revisan alertas de criticos,
        # que no dependen de que haya habido un escaneo local en este ciclo.
        return total_eventos
    if len(subnets) > 1:
        # Todas las subredes se escanean juntas en un solo pool de hilos
        # (scanner.scan_subnets) en vez de una despues de la otra -- si no,
        # con sitios remotos como Arica/Iquique de por medio, el ciclo
        # completo se estiraba varios minutos en vez de los 60s configurados
        # (visto en la practica el 2026-07-16: un equipo tardaba minutos de
        # mas en reflejar que estaba offline).
        nombres = ", ".join(sn["label"] for sn in subnets)
        log(f"[{hora}] Escaneando {len(subnets)} subredes en paralelo: {nombres}...")
        resultados_por_cidr = scanner.scan_subnets(subnets, config, max_workers)
    else:
        log(f"[{hora}] Escaneando {subnets[0]['cidr']} ({subnets[0]['label']})...")
        resultados_por_cidr = {subnets[0]["cidr"]: scanner.scan_subnet(subnets[0]["cidr"], config, max_workers)}

    resultados_combinados = []
    for sn in subnets:
        results = resultados_por_cidr[sn["cidr"]]
        resultados_combinados.extend(results)
        eventos = db.apply_scan_results(
            sn["cidr"], results, source="monitor", offline_after_misses=offline_after_misses,
            subred_label=sn.get("ciudad"),
        )
        total_eventos.extend(eventos)
        activos = sum(1 for r in results if r["alive"])
        log(f"  -> {sn['cidr']} ({sn['label']}): {activos}/{len(results)} activos")

    for ev in total_eventos:
        etiqueta = ETIQUETAS.get(ev["tipo"], ev["tipo"])
        log(f"  [{etiqueta}] {ev['ip']} ({ev['hostname'] or 'sin hostname'})")

    # Mismo escaneo, pero aplicado a los switches/routers de Infraestructura
    # (dispositivos_red) que tengan IP cargada -- puramente informativo para
    # la columna "En linea" de esa tabla, no genera alertas ni eventos de
    # "Ultimos cambios" (eso sigue siendo solo para equipos).
    cambios_red = db.aplicar_estado_red(resultados_combinados, offline_after_misses=offline_after_misses)
    for c in cambios_red:
        etiqueta = "VOLVIO ONLINE" if c["tipo"] == "online" else "PASO A OFFLINE"
        log(f"  [{etiqueta}] {c['ip']} (dispositivo de red: {c['nombre']})")

    return total_eventos


_ultima_revision_sitio = {}  # nombre del sitio -> timestamp del ultimo chequeo a Firebase


_sitio_ya_avisado_stale = set()  # nombres de sitio para los que ya se aviso "sin datos frescos" (no repetir cada ciclo)

# Workers para el escaneo DIRECTO de respaldo (ver mas abajo) -- deliberadamente
# bajo y fijo, sin importar lo que diga concurrency.max_workers en config.json:
# esto corre en el PC CENTRAL, y si varios sitios se ponen "stale" al mismo
# tiempo (ej. un corte de luz grande) no queremos sumarle carga fuerte al
# central justo cuando mas se lo necesita para seguir alertando.
_WORKERS_ESCANEO_DIRECTO = 30


def _sincronizar_sitios_remotos(config, max_workers=None):
    """Un colector sin datos no demuestra que sus equipos esten apagados."""
    ahora = time.time()
    for original in config.get("sitios_remotos", []):
        sitio = dict(original, offline_after_misses=config.get("monitor", {}).get("offline_after_misses", 2))
        nombre = sitio["nombre"]
        cada = sitio.get("revisar_cada_seg", 120)
        if ahora - _ultima_revision_sitio.get(nombre, 0) < cada:
            continue
        _ultima_revision_sitio[nombre] = ahora
        try:
            data = firebase_sync.leer_escaneo_remoto(nombre)
            resultados = data.get("hosts") if isinstance(data, dict) else None
            reporte = data.get("generated_at") if isinstance(data, dict) else None
            fecha = db.instante(reporte)
            red = ipaddress.ip_network(sitio["cidr"], strict=False)
            valido = isinstance(resultados, list) and bool(resultados) and fecha is not None
            if valido:
                try:
                    valido = all(isinstance(r, dict) and ipaddress.ip_address(r["ip"]) in red
                        and isinstance(r.get("alive"), (bool, int)) and r["alive"] in (True,False) for r in resultados)
                    valido = valido and (not data.get("cidr") or ipaddress.ip_network(data["cidr"], strict=False) == red)
                except (KeyError, ValueError, TypeError):
                    valido = False
            db.registrar_revision(sitio, reporte=reporte if valido else None,
                error=None if valido else "Sin respuesta o escaneo remoto incompleto")
            agente = next(a for a in db.list_agentes_escaneo() if a["nombre"] == nombre)
            if valido and db.estado_agente(reporte, cada) != "sin_datos":
                eventos = db.aplicar_escaneo_remoto(sitio, resultados, reporte)
                if eventos is not None:
                    log(f"  [FIREBASE] {nombre}: escaneo nuevo aplicado; {sum(bool(r['alive']) for r in resultados)}/{len(resultados)} activos")
                    for ev in eventos:
                        log(f"  [{ETIQUETAS.get(ev['tipo'], ev['tipo'])}] {ev['ip']} ({ev.get('hostname') or 'sin hostname'})")
                _sitio_ya_avisado_stale.discard(nombre)
                continue
            if agente["estado"] != "sin_datos":
                continue  # conserva la ultima observacion mientras siga dentro del margen
            if _en_misma_lan_servidor(sitio["cidr"]):
                try:
                    directo = scanner.scan_subnet(sitio["cidr"], config, _WORKERS_ESCANEO_DIRECTO)
                    if directo:
                        db.aplicar_escaneo_remoto(sitio, directo, datetime.now().isoformat(), respaldo=True)
                        log(f"  [RESPALDO LOCAL] {nombre}: agente sin reporte; red observada directamente")
                        continue
                except Exception as exc:
                    log(f"  [RESPALDO LOCAL] {nombre}: fallo ({type(exc).__name__})")
            db.marcar_subred_sin_datos(sitio["cidr"])
            if nombre not in _sitio_ya_avisado_stale:
                log(f"  [AGENTE SIN DATOS] {nombre}: se conserva el ultimo estado conocido; no se inventan desconexiones")
                _sitio_ya_avisado_stale.add(nombre)
        except Exception as exc:
            log(f"  [ERROR SITIO] {nombre}: {type(exc).__name__}: {exc}")
            db.registrar_revision(sitio, error="Error al consultar el colector")
            agente = next(a for a in db.list_agentes_escaneo() if a["nombre"] == nombre)
            if agente["estado"] == "sin_datos":
                db.marcar_subred_sin_datos(sitio["cidr"])


def _revisar_alertas_criticos(config):
    """Equipos marcados como 'critico' que llevan offline mas del umbral
    configurado y todavia no tienen el aviso mandado para esta caida."""
    umbral = config.get("alertas", {}).get("umbral_offline_min", 15)
    pendientes = db.equipos_criticos_pendientes_alerta(umbral_minutos=umbral)
    for equipo in pendientes:
        enviado = whatsapp_alertas.enviar_alerta_equipo_offline(equipo, config, log=log)
        if enviado:
            db.marcar_alerta_offline_enviada(equipo["id"])


def main():
    parser = argparse.ArgumentParser(description="Win NetWatch RMM - Monitor continuo")
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument("--local", action="store_true", help="Autodetecta y monitorea tu subred actual")
    group.add_argument("--subnet", metavar="CIDR", help="Monitorea una subred especifica")
    group.add_argument("--all", action="store_true", help="Monitorea todas las subredes de config.json")
    parser.add_argument("--interval", type=int, default=None, help="Segundos entre ciclos (default: config.json)")
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--once", action="store_true", help="Corre un solo ciclo y termina (para probar)")
    args = parser.parse_args()

    config = scanner.load_config()
    db.init_db()
    max_workers = args.workers or config.get("concurrency", {}).get("max_workers", 64)
    interval = args.interval or config.get("monitor", {}).get("interval_seconds", 60)

    # "ciudad" solo se guarda para equipos nuevos cuando el label viene de las
    # subredes reales de config.json (--all); "local"/"manual"/"predeterminada"
    # no son nombres de ciudad y no deberian terminar guardados como tal.
    subnets = []
    if args.local:
        cidr, _ip = scanner.detect_local_subnet()
        subnets.append({"cidr": cidr, "label": "local"})
    elif args.subnet:
        subnets.append({"cidr": args.subnet, "label": "manual"})
    elif args.all:
        subnets = [dict(sn, ciudad=sn.get("label")) for sn in config.get("subnets", [])]
    else:
        subnets.append({"cidr": config.get("default_subnet", "172.30.100.0/24"), "label": "predeterminada"})

    log(f"Monitor continuo iniciado. Intervalo: {interval}s. Ctrl+C para detener.")
    while True:
        for fase, accion in (
            ('escaneo local', lambda: run_once(subnets, config, max_workers)),
            ('sitios remotos', lambda: _sincronizar_sitios_remotos(config, max_workers)),
            ('alertas', lambda: _revisar_alertas_criticos(config)),
        ):
            try:
                accion()
            except Exception as e:
                log(f"[ERROR {fase}, se reintenta en {interval}s] {type(e).__name__}: {e}")
        if args.once:
            break
        log(f"Esperando {interval}s hasta el proximo ciclo...")
        time.sleep(interval)


if __name__ == "__main__":
    main()
