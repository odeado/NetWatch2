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
import firebase_sync  # noqa: E402  (de ../webapp, para leer sitios remotos sin VPN)

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
    hora = datetime.now().strftime("%H:%M:%S")
    if not subnets:
        # Puede quedar vacia si TODOS los sitios se alimentan por Firebase
        # (ver _sincronizar_sitios_remotos) y no hay ninguna subred que este
        # PC pueda escanear directo -- igual se revisan alertas de criticos,
        # que no dependen de que haya habido un escaneo local en este ciclo.
        _revisar_alertas_criticos(config)
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

    _revisar_alertas_criticos(config)

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
    """Sitios sin VPN permanente hacia este PC (Matta, y a futuro Arica/
    Iquique si tienen el mismo problema): en vez de escanearlos directo
    (fallaria la mayoria del tiempo sin la VPN conectada), se revisa lo
    ultimo que hayan publicado a Firebase (ver scanner/firebase_push.py).
    Cada sitio se revisa a SU PROPIO intervalo (mas espaciado que el
    escaneo local de aca), para no gastar la cuota gratis de Firebase --
    revisarlo cada ciclo de 60s del monitor seria demasiado."""
    offline_after_misses = config.get("monitor", {}).get("offline_after_misses", 2)
    ahora = time.time()
    for sitio in config.get("sitios_remotos", []):
        nombre = sitio["nombre"]
        cada = sitio.get("revisar_cada_seg", 120)
        if ahora - _ultima_revision_sitio.get(nombre, 0) < cada:
            continue
        _ultima_revision_sitio[nombre] = ahora

        data = firebase_sync.leer_escaneo_remoto(nombre)
        if not data:
            continue  # sin config de Firebase, sin internet, o el sitio no ha publicado nada todavia
        resultados = data.get("hosts")
        if not resultados:
            continue

        # OJO -- bug encontrado el 2026-07-20: si el scanner remoto de un
        # sitio se cae (PC apagado, tarea programada que dejo de correr,
        # etc.), el ultimo payload que alcanzo a publicar en Firebase se
        # queda ahi para siempre (Firebase no lo borra solo). Antes, cada
        # vez que este monitor central volvia a leer ESE MISMO payload viejo,
        # lo aplicaba igual -- y como apply_scan_results siempre pone
        # ultima_deteccion = ahora, un equipo que en realidad lleva dias
        # apagado se seguia viendo "en linea, visto hace 2 min" para siempre,
        # y como en_linea nunca pasaba a 0, la alerta de WhatsApp de "critico
        # caido hace mas de X min" tampoco se disparaba nunca. Ahora se
        # revisa que tan viejo es el payload (generated_at) antes de
        # aplicarlo: si es mas viejo que unos pocos ciclos de revision de ese
        # mismo sitio, se trata como "el scanner de ese sitio esta caido" y
        # se fuerza alive=False para todos sus hosts, para que entren al
        # conteo normal de fallos consecutivos y terminen marcandose offline
        # de verdad (y disparando la alerta si son criticos).
        generado_en = data.get("generated_at")
        antiguedad_seg = None
        if generado_en:
            try:
                antiguedad_seg = (datetime.now() - datetime.fromisoformat(generado_en)).total_seconds()
            except ValueError:
                antiguedad_seg = None
        antiguedad_max_seg = max(cada * 3, 600)  # al menos 10 min de margen

        if antiguedad_seg is not None and antiguedad_seg > antiguedad_max_seg:
            # OJO -- 2026-07-24: antes, si el scanner local de un sitio se
            # caia (el PC de esa sucursal se colgaba/apagaba/le sacaban el
            # .bat), el sitio ENTERO se veia offline aca aunque la red real
            # siguiera funcionando -- dependiamos 100% de que ese PC puntual
            # siguiera vivo. Ahora, antes de rendirnos, intentamos un escaneo
            # DIRECTO de esa subred desde el PC central (esto es lo que se
            # usaba originalmente, ver config.json). Si hay VPN manual
            # conectada hacia ese sitio en este momento, vemos el estado real
            # en vez de "todo offline" solo porque un PC puntual se colgo. Si
            # tambien falla (sin VPN conectada ahora mismo), recien ahi se
            # marca todo como no respondido, igual que antes.
            resultados_directos = None
            try:
                resultados_directos = scanner.scan_subnet(sitio["cidr"], config, _WORKERS_ESCANEO_DIRECTO)
            except Exception as e:
                log(f"  [ESCANEO DIRECTO FALLO] {nombre} ({sitio['cidr']}): {type(e).__name__}: {e}")
                resultados_directos = None

            activos_directo = sum(1 for r in resultados_directos if r["alive"]) if resultados_directos else 0

            if resultados_directos and activos_directo > 0:
                if nombre not in _sitio_ya_avisado_stale:
                    log(f"  [FIREBASE STALE -> ESCANEO DIRECTO OK] {nombre} ({sitio['cidr']}): el scanner "
                        f"remoto de ese sitio parece detenido, pero hay VPN conectada -- se usa el escaneo "
                        f"directo desde el PC central en su lugar ({activos_directo}/{len(resultados_directos)} activos).")
                    _sitio_ya_avisado_stale.add(nombre)
                resultados = resultados_directos
            else:
                if nombre not in _sitio_ya_avisado_stale:
                    log(f"  [FIREBASE STALE] {nombre} ({sitio['cidr']}): sin datos frescos hace "
                        f"{int(antiguedad_seg // 60)} min (publicado {generado_en}) -- el scanner remoto "
                        f"de ese sitio parece detenido y tampoco hay VPN conectada para verlo directo. "
                        f"Se marcan sus equipos como no respondidos.")
                    _sitio_ya_avisado_stale.add(nombre)
                resultados = [dict(r, alive=False) for r in resultados]
        else:
            _sitio_ya_avisado_stale.discard(nombre)

        eventos = db.apply_scan_results(
            sitio["cidr"], resultados, source=f"firebase:{nombre}",
            offline_after_misses=offline_after_misses, subred_label=sitio.get("ciudad"),
        )
        activos = sum(1 for r in resultados if r["alive"])
        log(f"  [FIREBASE] {nombre} ({sitio['cidr']}): {activos}/{len(resultados)} activos "
            f"(publicado {data.get('generated_at', '?')})")
        for ev in eventos:
            etiqueta = ETIQUETAS.get(ev["tipo"], ev["tipo"])
            log(f"  [{etiqueta}] {ev['ip']} ({ev['hostname'] or 'sin hostname'})")

        # Mismo resultado, aplicado tambien a los switches/routers de
        # Infraestructura de ESTE sitio (dispositivos_red con IP en este
        # CIDR) -- sitios remotos como Arica/Iquique/Matta no pasan por el
        # loop principal de run_once(), asi que sin esto sus switches se
        # quedaban en "sin datos" para siempre aunque el scanner remoto
        # SI los estuviera viendo.
        cambios_red = db.aplicar_estado_red(resultados, offline_after_misses=offline_after_misses)
        for c in cambios_red:
            etiqueta = "VOLVIO ONLINE" if c["tipo"] == "online" else "PASO A OFFLINE"
            log(f"  [{etiqueta}] {c['ip']} (dispositivo de red: {c['nombre']})")


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
        try:
            run_once(subnets, config, max_workers)
            _sincronizar_sitios_remotos(config, max_workers)
        except Exception as e:
            # Mismo bug encontrado en scanner.py (ver ahi el detalle): un
            # error de red al leer un sitio remoto de Firebase no quedaba
            # atrapado y podia matar este monitor CENTRAL entero -- mucho mas
            # grave que perder un solo sitio, porque tambien corta las
            # alertas de WhatsApp de TODOS los equipos criticos, no solo los
            # remotos. Ahora se registra el error y se sigue al proximo
            # ciclo en vez de morir.
            log(f"[ERROR EN EL CICLO, se reintenta en {interval}s] {type(e).__name__}: {e}")
        if args.once:
            break
        log(f"Esperando {interval}s hasta el proximo ciclo...")
        time.sleep(interval)


if __name__ == "__main__":
    main()
