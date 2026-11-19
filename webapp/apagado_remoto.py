"""Apagado remoto de equipos.

Mecanismo ACTUAL (recomendado, ver conversacion 2026-08-18): cola de
comandos via Firebase. El servidor deja una orden en
comandos_equipo/{ip} (firebase_sync.encolar_comando_equipo) y un agente
(scripts/netwatch_agente.ps1) que corre como tarea programada EN CADA PC
(desplegado por GPO) consulta esa cola cada ~30s y ejecuta el apagado
localmente. Todo el trafico lo inicia la PC hacia afuera (HTTPS saliente),
nunca el servidor hacia la PC -- asi se evita el problema real que se
encontro en la practica: el antivirus centralizado (ESET Endpoint Security)
bloquea las conexiones ENTRANTES (SMB/puerto 445) sin que Andres tenga
acceso a esa consola para hacer una excepcion. Ver encolar_comandos_equipos.

Mecanismo VIEJO (Fase 1 original, 2026-08-14, se deja el codigo por si
alguna red especifica NO tiene ESET bloqueando y de verdad prefieren la
via directa): net use + shutdown /m contra la LAN local de Rendic/Rendic2.
Requiere que el equipo destino tenga el firewall (Windows Y el antivirus
que lo administre) abierto para SMB, y una cuenta admin local compartida
(ver webapp/apagado_config.json, gitignored). En la practica esto fallo
por el bloqueo de ESET -- ver ejecutar_comando_equipos/_comando_equipo.
"""
import ipaddress
import json
import re
import subprocess
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import firebase_sync
from db import equipos as db_equipos

CONFIG_PATH = Path(__file__).resolve().parent / "apagado_config.json"

# Subredes cubiertas en esta fase -- coincide con "rendic"/"rendic2" en
# scanner/config.json. Si se agrega Fase 2, esta lista se puede ampliar (o
# reemplazar por una revision aparte para los equipos que van por Firebase).
REDES_LOCALES = [ipaddress.ip_network("172.30.100.0/24"), ipaddress.ip_network("172.30.101.0/24")]


class ApagadoConfigError(Exception):
    pass


def _cargar_config():
    if not CONFIG_PATH.exists():
        raise ApagadoConfigError("config_faltante")
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ApagadoConfigError("config_invalida")
    if not cfg.get("usuario") or not cfg.get("password"):
        raise ApagadoConfigError("config_incompleta")
    cfg.setdefault("aviso_segundos", 60)
    cfg.setdefault("mensaje", "Apagado programado desde NetWatch - Sistemas")
    return cfg


def leer_aviso_y_mensaje():
    """Lee solo aviso_segundos/mensaje de apagado_config.json, sin exigir
    usuario/password (esos ya no hacen falta para la via del agente/
    Firebase, solo los usaba el mecanismo SMB viejo). Si el archivo no
    existe o esta incompleto, devuelve los valores por defecto de siempre en
    vez de fallar -- encolar la orden no deberia depender de un archivo que
    ya es opcional para este camino."""
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    return (
        cfg.get("aviso_segundos", 60),
        cfg.get("mensaje", "Apagado programado desde NetWatch - Sistemas"),
    )


def ip_en_alcance_fase1(ip):
    """True si la IP esta dentro de las subredes que esta fase puede
    alcanzar directo (misma LAN que el servidor). Cualquier otra IP se
    rechaza explicitamente en vez de intentar igual y fallar silencioso."""
    try:
        direccion = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(direccion in red for red in REDES_LOCALES)


# Bandera de shutdown.exe por accion -- reiniciar es MENOS destructivo para
# probar que el mecanismo funciona (el equipo vuelve solo), asi que conviene
# probarlo primero con esto antes de usar "apagar" en serio.
ACCIONES = {"apagar": "/s", "reiniciar": "/r"}


def _variantes_usuario(usuario):
    """Windows en si no distingue mayus/minus para cuentas locales, pero la
    cuenta "sistemas" se fue creando a mano en cada PC con el tiempo y en
    algunos quedo como "Sistemas" (con mayuscula inicial). Para no depender
    de que todos coincidan exacto, probamos un par de variantes de
    capitalizacion antes de darnos por vencidos. Devuelve una lista sin
    duplicados, en el orden en que se van a probar."""
    variantes = []
    for candidato in (usuario, usuario.capitalize(), usuario.lower(), usuario.upper()):
        if candidato not in variantes:
            variantes.append(candidato)
    return variantes


def _diagnosticar_error_conexion(detalle_net_use):
    """"net use" devuelve texto de error variado segun la causa. Antes esta
    funcion asumia siempre "credenciales rechazadas", pero el error 67 (el
    mas comun en la practica) en realidad significa que el equipo destino
    ni siquiera dejo llegar la conexion SMB -- casi siempre porque el
    firewall de Windows tiene bloqueado "Uso compartido de archivos e
    impresoras" (puerto 445), no porque el usuario/clave este mal. Separar
    los dos casos evita mandar a activar la cuenta Administrador cuando en
    realidad el problema es el firewall."""
    texto = (detalle_net_use or "").lower()
    if re.search(r"\berror(es)? (de sistema )?67\b", texto) or "no se encuentra el nombre de red" in texto or "network name" in texto:
        return (
            "sin_ruta_de_red (el equipo no dejo llegar la conexion SMB -- revisa que en ese PC "
            "este activado 'Uso compartido de archivos e impresoras' en el Firewall de Windows, "
            "puerto 445; esto NO es un problema de usuario/clave)"
        )
    if re.search(r"\berror(es)? (de sistema )?53\b", texto):
        return "equipo_inalcanzable (error 53 -- el equipo esta apagado, fuera de la red, o el nombre/IP no resuelve)"
    if re.search(r"\berror(es)? (de sistema )?5\b", texto) or "acceso denegado" in texto or "access is denied" in texto:
        return (
            "acceso_denegado (la sesion SMB conecto pero la cuenta no tiene permiso -- "
            "revisa la clave LocalAccountTokenFilterPolicy si 'sistemas' no es la cuenta "
            "Administrador integrada, o que la clave este bien escrita en apagado_config.json)"
        )
    return "conexion_rechazada"


def _comando_equipo(ip, accion):
    """Intenta apagar o reiniciar UN equipo por su IP (accion: 'apagar' o
    'reiniciar'). Devuelve (ok: bool, detalle: str). Nunca lanza excepciones
    de sistema hacia afuera -- todo lo que puede fallar (falta config,
    comando no disponible, credenciales rechazadas, equipo apagado/
    inalcanzable) se devuelve como (False, motivo) para que el llamador
    pueda armar un resumen por equipo sin que un solo fallo tumbe el resto
    del lote."""
    if accion not in ACCIONES:
        return False, "accion_invalida"
    if not ip_en_alcance_fase1(ip):
        return False, "fuera_de_alcance_fase1"

    try:
        cfg = _cargar_config()
    except ApagadoConfigError as e:
        return False, str(e)

    recurso = f"\\\\{ip}\\IPC$"
    try:
        # Sesion SMB con la cuenta admin compartida -- necesaria porque la
        # cuenta con la que corre este servidor no necesariamente es admin
        # en el equipo destino (cada uno entraba con credenciales de
        # usuario distintas antes de esto). Probamos variantes de
        # mayuscula/minuscula del usuario hasta que una autentique.
        conectado = False
        ultimo_detalle_conexion = ""
        for usuario_intento in _variantes_usuario(cfg["usuario"]):
            resultado_use = subprocess.run(
                ["net", "use", recurso, cfg["password"], "/user:" + usuario_intento],
                capture_output=True, text=True, timeout=15,
            )
            if resultado_use.returncode == 0:
                conectado = True
                break
            ultimo_detalle_conexion = (resultado_use.stderr or resultado_use.stdout or "").strip()
            subprocess.run(["net", "use", recurso, "/delete"], capture_output=True, timeout=10)

        if not conectado:
            detalle = ultimo_detalle_conexion or "sin detalle"
            return False, f"{_diagnosticar_error_conexion(detalle)} -- {detalle}"

        resultado = subprocess.run(
            [
                "shutdown", ACCIONES[accion], "/m", f"\\\\{ip}",
                "/t", str(cfg["aviso_segundos"]),
                "/c", cfg["mensaje"],
            ],
            capture_output=True, text=True, timeout=15,
        )
    except FileNotFoundError:
        return False, "comando_no_disponible (net/shutdown -- solo funciona corriendo en Windows)"
    except subprocess.TimeoutExpired:
        return False, "timeout (el equipo no respondio a tiempo, puede estar apagado o inalcanzable)"
    finally:
        # Limpiar la sesion SMB pase lo que pase, para no dejar conexiones
        # colgadas acumulandose cada vez que se usa esta funcion.
        try:
            subprocess.run(["net", "use", recurso, "/delete"], capture_output=True, timeout=10)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    if resultado.returncode == 0:
        verbo = "apagado" if accion == "apagar" else "reinicio"
        return True, f"{verbo} programado en {cfg['aviso_segundos']}s"
    detalle = (resultado.stderr or resultado.stdout or "").strip() or f"codigo {resultado.returncode}"
    return False, detalle


def apagar_equipo(ip):
    return _comando_equipo(ip, "apagar")


def reiniciar_equipo(ip):
    return _comando_equipo(ip, "reiniciar")


def ejecutar_comando_equipos(equipos, accion):
    """equipos: lista de dicts con al menos 'id' e 'ip'. accion: 'apagar' o
    'reiniciar'. Devuelve un resumen
    {"ok": [...], "fuera_de_alcance": [...], "fallidos": [{"id","ip","motivo"}]}.

    Los fallos tambien se imprimen a consola (stdout, donde corre Flask)
    para poder diagnosticar sin tener que exponer el detalle completo en la
    URL/pantalla -- el resumen que ve el usuario en la web solo trae un
    extracto corto."""
    resumen = {"ok": [], "fuera_de_alcance": [], "fallidos": []}
    for e in equipos:
        ip = e.get("ip")
        if not ip:
            resumen["fallidos"].append({"id": e["id"], "ip": ip, "motivo": "sin_ip"})
            continue
        ok, detalle = _comando_equipo(ip, accion)
        if ok:
            resumen["ok"].append({"id": e["id"], "ip": ip})
        elif detalle == "fuera_de_alcance_fase1":
            resumen["fuera_de_alcance"].append({"id": e["id"], "ip": ip})
        else:
            resumen["fallidos"].append({"id": e["id"], "ip": ip, "motivo": detalle})
            print(f"[apagado_remoto] fallo {accion} en {ip} (equipo id {e['id']}): {detalle}")
    return resumen


# Alias retrocompatible (por si algo mas queda importando el nombre viejo).
def apagar_equipos(equipos):
    return ejecutar_comando_equipos(equipos, "apagar")


def encolar_comandos_equipos(equipos, accion, aviso_segundos=60, mensaje=None, on_progreso=None):
    """Version nueva (via agente/Firebase, ver docstring del modulo) del
    mismo lote que ejecutar_comando_equipos hacia arriba. No hay concepto de
    "fuera de alcance" aca -- Firebase se puede leer desde cualquier red con
    internet, asi que en teoria alcanza cualquier PC. En la practica solo
    "hace algo" en las PCs donde ya este instalado y corriendo el agente
    (netwatch_agente.ps1); si el agente no esta desplegado en un equipo, la
    orden queda encolada sin que nadie la recoja -- no hay forma de saberlo
    desde aca sin que el agente tambien reporte un "ultima vez visto"
    (pendiente si hace falta mas adelante).

    Devuelve {"ok": [...], "fallidos": [{"id","ip","motivo"}]}."""
    resumen = {"ok": [], "fallidos": []}
    for e in equipos:
        ip = e.get("ip")
        if not ip:
            resumen["fallidos"].append({"id": e["id"], "ip": ip, "motivo": "sin_ip"})
            continue
        # id propio de cada orden: el agente lo devuelve en
        # agentes_estado/{ip}/ultima_orden, y asi se sabe CUAL orden se ejecuto
        # (y con que resultado) en vez de deducirlo de que la orden desaparecio.
        orden_id = uuid.uuid4().hex[:12]
        ok, detalle = firebase_sync.encolar_comando_equipo(
            ip, accion, aviso_segundos, mensaje, orden_id=orden_id, caduca_seg=ORDEN_CADUCA_SEG)
        if ok:
            resumen["ok"].append({"id": e["id"], "ip": ip, "orden_id": orden_id})
        else:
            resumen["fallidos"].append({"id": e["id"], "ip": ip, "motivo": detalle})
            print(f"[apagado_remoto] no se pudo encolar {accion} en {ip} (equipo id {e['id']}): {detalle}")
        if on_progreso:
            on_progreso(e, ok, detalle if not ok else None)
    return resumen


# --- estado compartido para la barra de progreso ---------------------------
# Mismo patron que firebase_sync._estado/obtener_estado: cada equipo se
# encola con UNA llamada HTTP a Firebase (ver encolar_comando_equipo), y esa
# llamada puede demorar unos cientos de ms -- con pocos equipos no se nota,
# pero con un lote grande el navegador se quedaba pegado varios segundos
# sin ningun aviso de que seguia trabajando. Andres pidio ver el avance en
# vivo (2026-08-24), asi que el encolado ahora corre en un hilo aparte y la
# pagina hace poll a /admin/equipos/apagar_masivo/estado mientras tanto.
#
# Ademas hay una FASE 2 despues de encolar: "confirmando". Andres pregunto
# el mismo dia como saber si el equipo de verdad se reinicio, y con razon --
# hasta ahora "encolado" solo decia que la orden llego a Firebase, no que
# alguna PC la haya ejecutado. El agente (netwatch_agente.ps1, paso 3) BORRA
# el nodo comandos_equipo/{ip} apenas la ejecuta, exitosa o no -- asi que si
# ese nodo desaparece, es la mejor senal disponible de "un agente la recogio
# y corrio". Se revisa cada pocos segundos con un plazo maximo fijo (no se
# queda esperando para siempre): pasado ese plazo, lo que no se confirmo
# probablemente sea una PC sin el agente instalado o apagada.
#
# Y una FASE 3, solo para "reiniciar": "esperando_vuelta". Andres hizo notar
# que un reinicio real dura menos que el intervalo del scanner (60-120s), asi
# que el panel de "Ultimos cambios" casi nunca alcanza a pillar el "Paso a
# offline" de un reinicio rapido -- el scanner directamente se lo salta. El
# agente si sirve para esto: apenas Windows termina de bootear, la tarea
# programada vuelve a correr (cada 1 min) y deja un heartbeat fresco en
# agentes_estado/{ip} (ver firebase_sync.leer_agentes_estado). Si ese
# heartbeat es MAS NUEVO que el momento en que se confirmo la ejecucion, es
# una senal directa de "esta PC ya termino de reiniciar y esta de vuelta",
# sin depender para nada del ciclo del scanner. Tambien tiene plazo maximo
# (el boot + el primer ciclo del agente puede tardar unos minutos).
_estado_lock = threading.Lock()
_estado = {
    "corriendo": False, "fase": "inactivo", "total": 0, "procesados": 0,
    "total_confirmar": 0, "confirmados": 0, "total_volver": 0, "vueltos": 0,
    "accion": None, "resumen": None,
}

CONFIRMAR_TIMEOUT_SEG = 120
# Plazo de vida de una orden en la cola: el agente la descarta si es mas vieja
# (PC apagada al encolar) y, ademas, lo que no se confirmo en
# CONFIRMAR_TIMEOUT_SEG se borra de la cola desde aca (ver _en_hilo).
ORDEN_CADUCA_SEG = 600
# Un reinicio real mueve la hora de arranque (arranque_en) del agente al menos
# esto hacia adelante. Margen para el "tembleque" de LastBootUpTime.
ARRANQUE_MIN_DIF_SEG = 30
CONFIRMAR_INTERVALO_SEG = 4
VOLVER_TIMEOUT_SEG = 8 * 60
VOLVER_INTERVALO_SEG = 10
# Margen minimo despues del apagado/reinicio programado (aviso_segundos)
# antes de aceptar CUALQUIER heartbeat del agente como senal de "volvio".
# Sin esto, el heartbeat que sigue llegando cada 1 min DURANTE la cuenta
# regresiva de shutdown.exe (antes de que Windows realmente se reinicie) ya
# es "mas nuevo" que confirmado_en y marcaba "ya volvio" de forma casi
# instantanea -- visto en la practica el 2026-09-09: el panel decia "ya
# volvio a conectarse" mientras la pantalla remota todavia mostraba
# "Apagando el equipo...". 45s cubre POST + arranque rapido de Windows +
# que la tarea programada del agente vuelva a correr.
MARGEN_REBOOT_SEG = 45


def _parse_utc_agente(iso):
    """El agente escribe visto_en como 'yyyy-MM-ddTHH:mm:ssZ' (UTC, con Z
    literal) -- datetime.fromisoformat de Python 3.10 no acepta ese sufijo
    directo, asi que se cambia por +00:00 antes de parsear."""
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None


def _visto_utc(info):
    """Ultima vez que el agente reporto, como datetime UTC. Prefiere la hora
    del SERVIDOR de Firebase (visto_en_srv, milisegundos) sobre la del reloj
    de la PC (visto_en), que en PCs con la pila de la BIOS agotada puede estar
    cualquier dia. Agentes viejos solo traen visto_en."""
    if not info:
        return None
    ms = info.get("visto_en_srv")
    if isinstance(ms, (int, float)):
        try:
            return datetime.fromtimestamp(ms / 1000, timezone.utc)
        except (OverflowError, OSError, ValueError):
            pass
    return _parse_utc_agente(info.get("visto_en"))


def obtener_estado_encolado():
    with _estado_lock:
        return dict(_estado)


def iniciar_encolado_async(equipos, accion, aviso_segundos=60, mensaje=None):
    """Arranca el encolado (y despues la confirmacion) en un hilo aparte.
    Devuelve False si ya habia uno corriendo (para no mezclar dos lotes a
    la vez)."""
    with _estado_lock:
        if _estado["corriendo"]:
            return False
        _estado.update(corriendo=True, fase="encolando", total=len(equipos), procesados=0,
                        total_confirmar=0, confirmados=0, total_volver=0, vueltos=0,
                        accion=accion, resumen=None)

    def _avance(equipo, ok, motivo):
        with _estado_lock:
            _estado["procesados"] += 1

    def _en_hilo():
        # Foto previa: hora de arranque de Windows de cada PC ANTES de la orden.
        # Despues de reiniciar, el agente reporta otra distinta -- esa es la
        # senal definitiva de "volvio" (no depende del reloj de la PC ni del
        # ciclo del scanner).
        agentes_previos = firebase_sync.leer_agentes_estado()
        arranque_previo = {}
        for e in equipos:
            info_previa = agentes_previos.get(e.get("ip")) or {}
            arranque_previo[e.get("ip")] = _parse_utc_agente(info_previa.get("arranque_en"))

        resumen = encolar_comandos_equipos(equipos, accion, aviso_segundos, mensaje, on_progreso=_avance)

        ordenes = {x["ip"]: x["orden_id"] for x in resumen["ok"]}
        ips_por_confirmar = list(ordenes)
        with _estado_lock:
            _estado.update(fase="confirmando", total_confirmar=len(ips_por_confirmar), confirmados=0)

        confirmados = set()
        con_error = {}  # ip -> detalle: el agente la recibio pero fallo (o la descarto)
        ausentes = {}  # ip -> veces seguidas que la orden ya no estaba en la cola
        confirmado_en = {}  # ip -> momento (UTC) en que se detecto la ejecucion
        vuelta_minima_en = {}  # ip -> momento (UTC) desde el que un heartbeat cuenta como "volvio"

        def _confirmar(ip):
            ahora = datetime.now(timezone.utc)
            confirmados.add(ip)
            confirmado_en[ip] = ahora
            vuelta_minima_en[ip] = ahora + timedelta(seconds=aviso_segundos + MARGEN_REBOOT_SEG)

        intentos = max(1, int(CONFIRMAR_TIMEOUT_SEG // CONFIRMAR_INTERVALO_SEG))
        for _ in range(intentos):
            por_resolver = [ip for ip in ips_por_confirmar if ip not in confirmados and ip not in con_error]
            if not por_resolver:
                break
            time.sleep(CONFIRMAR_INTERVALO_SEG)
            claves_pendientes = set(firebase_sync.leer_comandos_pendientes().keys())
            agentes = firebase_sync.leer_agentes_estado()
            for ip in por_resolver:
                info = agentes.get(ip) or {}
                ultima = info.get("ultima_orden")
                if isinstance(ultima, dict) and ultima.get("id") == ordenes[ip]:
                    # Resultado REAL reportado por el agente para ESTA orden.
                    if ultima.get("resultado") == "ok":
                        _confirmar(ip)
                    else:
                        con_error[ip] = ultima.get("detalle") or ultima.get("resultado") or "error"
                elif ip.replace(".", "_") not in claves_pendientes:
                    # La orden ya no esta en la cola pero no hay resultado: es un
                    # agente viejo (no reporta resultado; ahi "borrada" es lo unico
                    # que hay) o el reporte del resultado fallo -- tras 3 revisiones
                    # seguidas se da por ejecutada.
                    ausentes[ip] = ausentes.get(ip, 0) + 1
                    if not info.get("agente_version") or ausentes[ip] >= 3:
                        _confirmar(ip)
                else:
                    ausentes[ip] = 0
            with _estado_lock:
                _estado["confirmados"] = len(confirmados) + len(con_error)

        # Lo que nadie recogio en el plazo se BORRA de la cola: si no, una PC
        # apagada (o sin agente) ejecutaria el reinicio horas despues, apenas
        # alguien la prenda.
        sin_confirmar = sorted(set(ips_por_confirmar) - confirmados - set(con_error))
        canceladas = []
        for ip in sin_confirmar:
            cancelada, _motivo = firebase_sync.cancelar_comando_equipo(ip)
            if cancelada:
                canceladas.append(ip)

        resumen["confirmados"] = sorted(confirmados)
        resumen["sin_confirmar"] = sin_confirmar
        resumen["canceladas"] = canceladas
        resumen["con_error"] = [{"ip": ip, "detalle": d} for ip, d in sorted(con_error.items())]

        # Fase 3, solo tiene sentido para "reiniciar" -- un equipo apagado
        # no vuelve solo, asi que esperar a que "vuelva" no aplica.
        vueltos = set()
        if accion == "reiniciar" and confirmados:
            with _estado_lock:
                _estado.update(fase="esperando_vuelta", total_volver=len(confirmados), vueltos=0)
            intentos_volver = max(1, int(VOLVER_TIMEOUT_SEG // VOLVER_INTERVALO_SEG))
            for _ in range(intentos_volver):
                if len(vueltos) >= len(confirmados):
                    break
                time.sleep(VOLVER_INTERVALO_SEG)
                agentes = firebase_sync.leer_agentes_estado()
                for ip in confirmados:
                    if ip in vueltos:
                        continue
                    info = agentes.get(ip)
                    visto = _visto_utc(info)
                    # El heartbeat que sigue llegando DURANTE la cuenta regresiva de
                    # shutdown.exe (ver MARGEN_REBOOT_SEG) no cuenta como "volvio".
                    if not visto or visto <= vuelta_minima_en[ip]:
                        continue

                    nuevo_arranque = _parse_utc_agente(info.get("arranque_en"))
                    previo = arranque_previo.get(ip)
                    if nuevo_arranque and previo:
                        # Senal definitiva: Windows arranco de nuevo (la hora de
                        # arranque que reporta el agente cambio).
                        equipo_vuelto = nuevo_arranque > previo + timedelta(seconds=ARRANQUE_MIN_DIF_SEG)
                    else:
                        # Sin hora de arranque previa (agente viejo): heartbeat nuevo
                        # + que el scanner lo vea online en la BD.
                        equipo_vuelto = False
                        equipo_id = db_equipos.get_equipo_by_ip(ip)
                        if equipo_id:
                            equipo = db_equipos.get_equipo(equipo_id)
                            if equipo and equipo.get('en_linea'):
                                equipo_vuelto = True

                    if equipo_vuelto:
                        vueltos.add(ip)
                with _estado_lock:
                    _estado["vueltos"] = len(vueltos)
            resumen["vueltos"] = sorted(vueltos)
            resumen["sin_volver"] = sorted(confirmados - vueltos)

        with _estado_lock:
            _estado.update(corriendo=False, fase="listo", resumen=resumen)

    threading.Thread(target=_en_hilo, daemon=True).start()
    return True
