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
from pathlib import Path

import firebase_sync

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


def encolar_comandos_equipos(equipos, accion, aviso_segundos=60, mensaje=None):
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
        ok, detalle = firebase_sync.encolar_comando_equipo(ip, accion, aviso_segundos, mensaje)
        if ok:
            resumen["ok"].append({"id": e["id"], "ip": ip})
        else:
            resumen["fallidos"].append({"id": e["id"], "ip": ip, "motivo": detalle})
            print(f"[apagado_remoto] no se pudo encolar {accion} en {ip} (equipo id {e['id']}): {detalle}")
    return resumen
