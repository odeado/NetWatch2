"""
Win NetWatch RMM - Empuje de escaneos remotos a Firebase
============================================================
Para sucursales sin VPN permanente hacia el PC central (Matta, Arica,
Iquique): en vez de esperar a que alguien se conecte por VPN o copie un
archivo a mano, esta pieza manda el resultado de cada escaneo directo a
Firebase Realtime Database apenas termina -- el PC central lo recoge solo,
sin depender de la VPN para nada (ver monitor.py, funcion
_sincronizar_sitios_remotos).

Manda solo lo esencial (ip, alive, hostname, mac, puertos, confianza) --
NADA de latencia/perdida de paquetes detallada -- para mantener el payload
chico y no gastar la cuota gratis de Firebase (el mismo cuidado que ya se
tuvo en webapp/firebase_sync.py, que a proposito deja el estado de escaneo
fuera de la sincronizacion de administracion).

Necesita su PROPIA copia de firebase_config.json en esta misma carpeta
(scanner/) -- copia el mismo archivo que ya usa la webapp
(webapp/firebase_config.json), con las mismas apiKey/databaseURL/email/
password. No se sube a git (mismo patron que el original).
"""

import json
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent / "firebase_config.json"


class FirebasePushError(Exception):
    pass


def _cargar_config():
    if not CONFIG_PATH.exists():
        raise FirebasePushError(
            "Falta scanner/firebase_config.json (copia el mismo archivo que usa la webapp)"
        )
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise FirebasePushError(f"firebase_config.json invalido: {e}")
    faltan = [k for k in ("apiKey", "databaseURL", "email", "password") if not cfg.get(k)]
    if faltan:
        raise FirebasePushError(f"firebase_config.json incompleto, falta: {', '.join(faltan)}")
    cfg["databaseURL"] = cfg["databaseURL"].rstrip("/")
    return cfg


def _http_json(url, method="GET", body=None, timeout=15):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raise FirebasePushError(f"HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:200]}")
    except urllib.error.URLError as e:
        raise FirebasePushError(f"conexion: {e.reason}")
    except OSError as e:
        # OJO -- bug real encontrado el 2026-07-20 en el sitio Rendic: un
        # corte breve de internet hizo que Firebase cortara la conexion a
        # mitad de la respuesta (http.client.RemoteDisconnected, que hereda
        # de OSError/ConnectionError, NO de urllib.error.URLError). Esa
        # excepcion no quedaba atrapada por los except de arriba, asi que se
        # propagaba sin control hasta matar el proceso scanner.py entero --
        # y como no hay nada que lo vuelva a levantar solo, el sitio se quedo
        # "congelado" (misma pantalla negra) hasta que alguien fue en persona
        # a reiniciarlo. Cualquier error de conexion (reset, timeout, fin de
        # conexion abrupto, etc.) ahora se atrapa aca tambien, para que el
        # llamador (publicar()) lo trate igual que cualquier otra falla de
        # red: avisa en el log y sigue al proximo ciclo, sin morir.
        raise FirebasePushError(f"conexion: {e}")
    return json.loads(raw) if raw else None


def _iniciar_sesion(cfg):
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={cfg['apiKey']}"
    resp = _http_json(
        url, method="POST",
        body={"email": cfg["email"], "password": cfg["password"], "returnSecureToken": True},
    )
    return resp["idToken"]


def _compactar(hosts):
    return [
        {
            "ip": h["ip"], "alive": h["alive"], "hostname": h.get("hostname"),
            "mac": h.get("mac"), "open_ports": h.get("open_ports", []),
            "confidence_score": h.get("confidence_score"),
            "confidence_label": h.get("confidence_label"),
            "metodo_deteccion": h.get("metodo_deteccion"),
        }
        for h in hosts
    ]


def publicar(sitio, all_results, log=print):
    """Sube el resultado de un escaneo al nodo escaneos_remotos/<sitio>.
    Nunca lanza excepciones -- si falla (sin config, sin internet, etc.)
    deja un aviso en el log y devuelve False; el proximo ciclo lo reintenta
    solo."""
    try:
        cfg = _cargar_config()
        id_token = _iniciar_sesion(cfg)
    except FirebasePushError as e:
        log(f"  [FIREBASE PUSH OMITIDO] {e}")
        return False

    # all_results viene como {cidr: [hosts]} (scanner.py escanea por subred),
    # pero cada sitio remoto solo tiene UNA subred -- se aplana a una lista
    # simple de hosts + el cidr como VALOR (no como clave), porque Firebase
    # Realtime Database prohibe "/" en los nombres de clave y un CIDR
    # (ej. "172.30.102.0/24") siempre tiene una.
    cidr, hosts = next(iter(all_results.items()))
    payload = {
        "generated_at": datetime.now().isoformat(),
        "cidr": cidr,
        "hosts": _compactar(hosts),
    }
    url = f"{cfg['databaseURL']}/escaneos_remotos/{sitio}.json?auth={id_token}"
    try:
        _http_json(url, method="PUT", body=payload)
    except FirebasePushError as e:
        log(f"  [FIREBASE PUSH FALLO] {e}")
        return False

    log(f"  [FIREBASE PUSH OK] {sitio}: {len(payload['hosts'])} hosts")
    return True
