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
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "webapp"))

import scanner  # noqa: E402  (scanner.py del mismo folder)
import db       # noqa: E402  (db.py de ../webapp)
import whatsapp_alertas  # noqa: E402  (mismo folder)

ETIQUETAS = {"online": "VOLVIO ONLINE", "offline": "PASO A OFFLINE", "nuevo": "NUEVO EQUIPO"}

# La webapp lee este archivo para mostrar la consola del monitor embebida en
# la pagina (en vez de tener que mirar la ventana negra aparte). Se recorta
# solo cuando crece mucho, para no dejarlo crecer para siempre en una maquina
# que queda prendida semanas.
LOG_FILE = Path(__file__).resolve().parent.parent / "webapp" / "monitor.log"
LOG_MAX_LINEAS = 4000
LOG_RECORTE_A = 2000


def log(msg):
    print(msg)
    try:
        linea = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(linea)
        _recortar_log_si_crecio_mucho()
    except OSError:
        pass  # si no se puede escribir el log no debe frenar el monitoreo


def _recortar_log_si_crecio_mucho():
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            lineas = f.readlines()
        if len(lineas) > LOG_MAX_LINEAS:
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.writelines(lineas[-LOG_RECORTE_A:])
    except OSError:
        pass


def run_once(subnets, config, max_workers):
    offline_after_misses = config.get("monitor", {}).get("offline_after_misses", 2)
    total_eventos = []
    for sn in subnets:
        hora = datetime.now().strftime("%H:%M:%S")
        log(f"[{hora}] Escaneando {sn['cidr']} ({sn['label']})...")
        results = scanner.scan_subnet(sn["cidr"], config, max_workers)
        eventos = db.apply_scan_results(
            sn["cidr"], results, source="monitor", offline_after_misses=offline_after_misses
        )
        total_eventos.extend(eventos)
        activos = sum(1 for r in results if r["alive"])
        log(f"  -> {activos}/{len(results)} activos")

    for ev in total_eventos:
        etiqueta = ETIQUETAS.get(ev["tipo"], ev["tipo"])
        log(f"  [{etiqueta}] {ev['ip']} ({ev['hostname'] or 'sin hostname'})")

    _revisar_alertas_criticos(config)

    return total_eventos


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

    subnets = []
    if args.local:
        cidr, _ip = scanner.detect_local_subnet()
        subnets.append({"cidr": cidr, "label": "local"})
    elif args.subnet:
        subnets.append({"cidr": args.subnet, "label": "manual"})
    elif args.all:
        subnets = config.get("subnets", [])
    else:
        subnets.append({"cidr": config.get("default_subnet", "172.30.100.0/24"), "label": "predeterminada"})

    log(f"Monitor continuo iniciado. Intervalo: {interval}s. Ctrl+C para detener.")
    while True:
        run_once(subnets, config, max_workers)
        if args.once:
            break
        log(f"Esperando {interval}s hasta el proximo ciclo...")
        time.sleep(interval)


if __name__ == "__main__":
    main()
