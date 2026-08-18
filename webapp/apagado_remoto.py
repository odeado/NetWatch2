"""Apagado remoto de equipos (Fase 1: solo la LAN local de Rendic/Rendic2,
la misma red donde corre este servidor -- ver conversacion 2026-08-14).

Usa los comandos nativos de Windows (net use + shutdown /m), NO requiere
instalar nada en los equipos destino. Necesita que:
  - El equipo destino tenga habilitado "compartir archivos e impresoras" /
    la excepcion de firewall de apagado remoto (suele venir activada si el
    equipo esta en un grupo de trabajo con recursos compartidos).
  - Exista una cuenta administradora LOCAL con el MISMO usuario/contrasena
    en todos los equipos de la red (ver webapp/apagado_config.json,
    gitignored -- nunca se sube a git, igual que firebase_config.json).

Fase 2 (pendiente, no implementada todavia): equipos en Arica/Iquique/Matta,
que no tienen VPN permanente hacia este servidor -- para esos habria que
avisar por Firebase y que el colector de esa ciudad ejecute el apagado
localmente dentro de su propia LAN (mismo patron que ya usa el escaneo,
ver firebase_sync.py / scanner/config.json "sitios_remotos").
"""
import ipaddress
import json
import subprocess
from pathlib import Path

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
        # usuario distintas antes de esto).
        subprocess.run(
            ["net", "use", recurso, cfg["password"], "/user:" + cfg["usuario"]],
            capture_output=True, text=True, timeout=15,
        )
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
    {"ok": [...], "fuera_de_alcance": [...], "fallidos": [{"id","ip","motivo"}]}."""
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
    return resumen


# Alias retrocompatible (por si algo mas queda importando el nombre viejo).
def apagar_equipos(equipos):
    return ejecutar_comando_equipos(equipos, "apagar")
