"""
NetWatch Colector -- vigilante liviano, independiente del sistema principal
===========================================================================
Corre 24/7 en cualquier PC con internet (no necesita la web, la base de datos
ni tu PC central encendido). No escanea nada: lee los escaneos que los
scanners de cada sitio (Rendic, Rendic2, Matta, Arica, Iquique) ya publican en
Firebase (nodo escaneos_remotos/<sitio>, ver scanner/firebase_push.py), los
compara con el escaneo anterior y publica lo que cambio:

  colector/eventos   un registro por cada cambio (cayo / volvio / nuevo /
                     sitio sin datos / sitio recuperado), con el flag
                     "critico" para los equipos de prioridad.
  colector/estado    latido del colector (cada ciclo) + resumen por sitio.
  colector/criticos  estado actual de los equipos criticos.

La app del celular (colector/android) consulta esos tres nodos y avisa con
notificaciones, igual que la app de Portadas.

Los equipos de prioridad son los que tienen critico=1 en NetWatch (nodo
equipos de Firebase, se relee cada hora y se guarda en cache) mas la lista
manual "criticos_manuales" del archivo de configuracion.

Solo usa la libreria estandar de Python (compatible con 3.8, la ultima version
que corre en Windows 7).
"""

import argparse
import json
import logging
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

VERSION = "1.1.0"
CARPETA = Path(__file__).resolve().parent
RUTA_CONFIG = CARPETA / "colector_config.json"
RUTA_ESTADO = CARPETA / "colector_estado.json"
RUTA_LOG = CARPETA / "colector.log"

SERVER_TS = {".sv": "timestamp"}  # Firebase lo reemplaza por su hora (ms)

POR_DEFECTO = {
    "intervalo_seg": 60,
    # Escaneos seguidos sin respuesta para dar un equipo por caido (igual que
    # monitor.offline_after_misses del sistema principal).
    "offline_tras_escaneos": 2,
    # Minutos sin que un sitio publique un escaneo nuevo para avisar que su
    # scanner dejo de reportar (los sitios publican cada ~2 min).
    "sitio_sin_datos_min": 10,
    "refrescar_criticos_min": 60,
    "max_eventos": 300,
    "sitios": [],  # vacio = todos los que existan en escaneos_remotos
    "criticos_manuales": [],
}

log = logging.getLogger("colector")


# --------------------------------------------------------------------------
# Firebase (REST, solo urllib)
# --------------------------------------------------------------------------
class FirebaseError(Exception):
    pass


class Firebase:
    def __init__(self, cfg):
        faltan = [k for k in ("databaseURL", "apiKey", "email", "password") if not cfg.get(k)]
        if faltan:
            raise FirebaseError("colector_config.json: falta firebase." + ", firebase.".join(faltan))
        self.db = cfg["databaseURL"].rstrip("/")
        self.api_key = cfg["apiKey"]
        self.email = cfg["email"]
        self.password = cfg["password"]
        self._id_token = None
        self._refresh_token = None
        self._vence = 0.0

    # --- autenticacion: se loguea UNA vez y despues renueva con el refresh
    # token (evita agotar la cuota de "verifyPassword", ver agente_apagado) ---
    def _post(self, url, cuerpo, formulario=False):
        if formulario:
            datos = urllib.parse.urlencode(cuerpo).encode("utf-8")
            cab = {"Content-Type": "application/x-www-form-urlencoded"}
        else:
            datos = json.dumps(cuerpo).encode("utf-8")
            cab = {"Content-Type": "application/json"}
        return self._abrir(urllib.request.Request(url, data=datos, method="POST", headers=cab), 20)

    def _token(self):
        if self._id_token and time.time() < self._vence - 300:
            return self._id_token
        if self._refresh_token:
            try:
                r = self._post("https://securetoken.googleapis.com/v1/token?key=" + self.api_key,
                               {"grant_type": "refresh_token", "refresh_token": self._refresh_token}, True)
                self._id_token = r["id_token"]
                self._refresh_token = r.get("refresh_token", self._refresh_token)
                self._vence = time.time() + int(r.get("expires_in", 3600))
                return self._id_token
            except (FirebaseError, KeyError, ValueError):
                self._refresh_token = None  # se cae a login con password
        r = self._post("https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=" + self.api_key,
                       {"email": self.email, "password": self.password, "returnSecureToken": True})
        try:
            self._id_token = r["idToken"]
        except KeyError:
            raise FirebaseError("login sin idToken")
        self._refresh_token = r.get("refreshToken")
        self._vence = time.time() + int(r.get("expiresIn", 3600))
        return self._id_token

    @staticmethod
    def _abrir(req, timeout):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                crudo = resp.read()
        except urllib.error.HTTPError as e:
            raise FirebaseError("HTTP %s: %s" % (e.code, e.read().decode("utf-8", "ignore")[:200]))
        except urllib.error.URLError as e:
            raise FirebaseError("conexion: %s" % (e.reason,))
        except OSError as e:
            # corte a mitad de respuesta (RemoteDisconnected, timeout, ...): no es URLError
            raise FirebaseError("conexion: %s" % (e,))
        try:
            return json.loads(crudo) if crudo else None
        except ValueError:
            raise FirebaseError("respuesta no es JSON")

    def pedir(self, metodo, ruta, cuerpo=None, query=None, timeout=25):
        for intento in (1, 2):
            q = dict(query or {})
            q["auth"] = self._token()
            url = "%s/%s.json?%s" % (self.db, ruta, urllib.parse.urlencode(q))
            datos = json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else None
            req = urllib.request.Request(url, data=datos, method=metodo,
                                         headers={"Content-Type": "application/json"})
            try:
                return self._abrir(req, timeout)
            except FirebaseError as e:
                if intento == 1 and "HTTP 401" in str(e):
                    self._id_token = None  # token vencido o revocado: se pide otro y se reintenta
                    self._vence = 0.0
                    continue
                raise

    def get(self, ruta, **q):
        return self.pedir("GET", ruta, query=q or None)

    def put(self, ruta, cuerpo):
        return self.pedir("PUT", ruta, cuerpo)

    def post(self, ruta, cuerpo):
        return self.pedir("POST", ruta, cuerpo)

    def patch(self, ruta, cuerpo):
        return self.pedir("PATCH", ruta, cuerpo)


# --------------------------------------------------------------------------
# Logica del colector
# --------------------------------------------------------------------------
def _clave_ip(ip):
    return ip.replace(".", "_")


def _verdadero(v):
    return v in (1, True, "1", "true", "True", "si", "SI")


def _lista_hosts(nodo):
    """Firebase devuelve una lista... o un dict con claves 0,1,2 si faltan
    huecos. Normaliza a lista de dicts."""
    hosts = nodo.get("hosts") if isinstance(nodo, dict) else None
    if isinstance(hosts, dict):
        hosts = list(hosts.values())
    return [h for h in (hosts or []) if isinstance(h, dict) and h.get("ip")]


class Colector:
    def __init__(self, fb, cfg, estado, publicar=True, reloj=time.time):
        self.fb = fb
        self.cfg = cfg
        self.estado = estado
        self.publicar = publicar
        self.reloj = reloj
        for clave in ("equipos", "sitios", "criticos", "pendientes"):
            self.estado.setdefault(clave, {} if clave != "pendientes" else [])
        self.estado.setdefault("ciclo", 0)
        self.estado.setdefault("ultima_poda", 0)

    # ---- criticos -------------------------------------------------------
    def _refrescar_criticos(self, forzar=False):
        crit = self.estado["criticos"]
        cada = self.cfg["refrescar_criticos_min"] * 60
        if not forzar and self.reloj() - crit.get("actualizado", 0) < cada:
            return
        try:
            nodo = self.fb.get("equipos")
        except FirebaseError as e:
            log.warning("No se pudo leer equipos (criticos): %s -- se usa la lista guardada", e)
            crit["actualizado"] = self.reloj() - cada + 300  # reintenta en 5 min, no cada ciclo
            return
        ips, nombres, responsables = [], {}, {}
        for v in (nodo or {}).values() if isinstance(nodo, dict) else (nodo or []):
            if isinstance(v, dict) and v.get("ip"):
                ip_v = v["ip"]
                if _verdadero(v.get("critico")):
                    ips.append(ip_v)
                if v.get("hostname"):
                    nombres[ip_v] = v["hostname"]
                if v.get("responsable"):
                    responsables[ip_v] = v["responsable"]
        crit.update(ips=sorted(set(ips)), nombres=nombres, responsables=responsables, actualizado=self.reloj())
        log.info("Equipos criticos: %d (de NetWatch) + %d manuales", len(crit["ips"]),
                 len(self.cfg["criticos_manuales"]))

    def _es_critico(self, ip):
        return ip in self.estado["criticos"].get("ips", []) or ip in self.cfg["criticos_manuales"]

    def _todos_los_criticos(self):
        return sorted(set(self.estado["criticos"].get("ips", [])) | set(self.cfg["criticos_manuales"]))

    # ---- eventos --------------------------------------------------------
    def _evento(self, lista, tipo, ip=None, hostname=None, sitio=None, **extra):
        ev = {"tipo": tipo, "sitio": sitio, "ts": SERVER_TS}
        if ip:
            ev.update(ip=ip,
                      hostname=hostname or self.estado["criticos"].get("nombres", {}).get(ip) or "",
                      responsable=self.estado["criticos"].get("responsables", {}).get(ip) or "",
                      critico=self._es_critico(ip))
        ev.update(extra)
        lista.append(ev)
        log.info("[%s] %s %s %s", tipo.upper(), sitio or "", ip or "",
                 ("(%s)" % ev["hostname"]) if ip and ev.get("hostname") else "")

    # ---- un sitio -------------------------------------------------------
    def _procesar_sitio(self, nombre, eventos):
        ahora = self.reloj()
        sit = self.estado["sitios"].setdefault(
            nombre, {"generated_at": None, "cambio_ts": ahora, "sin_datos": False, "activos": 0, "total": 0,
                     "conocido": False})
        gen = self.fb.get("escaneos_remotos/%s/generated_at" % nombre)
        if gen is None:
            gen_str = None
        else:
            gen_str = str(gen)

        if gen_str is not None and gen_str != sit["generated_at"]:
            nuevo_escaneo = True
            sit["cambio_ts"] = ahora
        else:
            nuevo_escaneo = False

        # Sitio que dejo de publicar: se mide con la hora de ESTE PC (cuando
        # cambio por ultima vez generated_at), no con el reloj del sitio.
        minutos_sin_cambio = (ahora - sit["cambio_ts"]) / 60.0
        if minutos_sin_cambio > self.cfg["sitio_sin_datos_min"]:
            if not sit["sin_datos"]:
                sit["sin_datos"] = True
                self._evento(eventos, "sitio_sin_datos", sitio=nombre, minutos=int(minutos_sin_cambio))
            return
        if sit["sin_datos"] and nuevo_escaneo:
            sit["sin_datos"] = False
            self._evento(eventos, "sitio_recuperado", sitio=nombre)
        if not nuevo_escaneo:
            return

        nodo = self.fb.get("escaneos_remotos/%s" % nombre)
        hosts = _lista_hosts(nodo)
        if not hosts:
            return
        primera_vez = not sit["conocido"]  # primer escaneo del sitio: solo se toma la foto, sin avisar nada
        equipos = self.estado["equipos"]
        umbral = self.cfg["offline_tras_escaneos"]

        for h in hosts:
            ip, vivo, nombre_host = h["ip"], bool(h.get("alive")), h.get("hostname") or ""
            e = equipos.get(ip)
            if vivo:
                if e is None:
                    equipos[ip] = {"online": True, "misses": 0, "desde": ahora, "hostname": nombre_host, "sitio": nombre}
                    if not primera_vez:
                        self._evento(eventos, "nuevo", ip, nombre_host, nombre)
                    continue
                if nombre_host:
                    e["hostname"] = nombre_host
                e["sitio"] = nombre
                e["misses"] = 0
                if not e["online"]:
                    dur = int(ahora - e["desde"])
                    e.update(online=True, desde=ahora)
                    if not primera_vez:
                        self._evento(eventos, "volvio", ip, e["hostname"], nombre, offline_seg=dur)
            elif e is not None and e["online"]:
                e["misses"] += 1
                if e["misses"] >= umbral:
                    e.update(online=False, desde=ahora)
                    if not primera_vez:
                        self._evento(eventos, "cayo", ip, e["hostname"], nombre)

        sit.update(generated_at=gen_str, conocido=True, activos=sum(1 for h in hosts if h.get("alive")),
                   total=len(hosts), cidr=(nodo.get("cidr") if isinstance(nodo, dict) else None) or "")
        log.info("Sitio %s: %d/%d activos%s", nombre, sit["activos"], sit["total"],
                 " (primer escaneo: solo se toma la foto, sin avisos)" if primera_vez else "")

    # ---- publicacion ----------------------------------------------------
    def _etiqueta_sitio(self, nombre, cidr):
        """'Iquique 112' = nombre del sitio + 3er octeto de su red (como los gauges del panel).
        Se puede forzar con "etiquetas": {"iquique": "Iquique 112"} en el config."""
        manual = (self.cfg.get("etiquetas") or {}).get(nombre)
        if manual:
            return manual
        partes = (cidr or "").split("/")[0].split(".")
        base = nombre[:1].upper() + nombre[1:]
        return "%s %s" % (base, partes[2]) if len(partes) == 4 and partes[2] else base

    def _estado_criticos(self):
        salida = {}
        for ip in self._todos_los_criticos():
            e = self.estado["equipos"].get(ip)
            salida[_clave_ip(ip)] = {
                "ip": ip,
                "hostname": (e or {}).get("hostname") or self.estado["criticos"].get("nombres", {}).get(ip) or "",
                "online": e["online"] if e else None,  # None = nunca visto en un escaneo
                "desde_ms": int(e["desde"] * 1000) if e else None,
                "sitio": (e or {}).get("sitio"),
            }
        return salida

    def _publicar(self, eventos):
        pend = self.estado["pendientes"]
        pend.extend(eventos)
        del pend[:-500]  # tope: si Firebase estuvo caido mucho rato no crece sin limite
        if not self.publicar:
            return
        enviados = 0
        while pend:
            try:
                self.fb.post("colector/eventos", pend[0])
            except FirebaseError as e:
                log.warning("No se pudo publicar un evento (%s); %d quedan en cola para el proximo ciclo",
                            e, len(pend))
                break
            pend.pop(0)
            enviados += 1

        equipos = self.estado["equipos"]
        crit = self._estado_criticos()
        resumen = {
            "version": VERSION,
            "ultima_conexion": SERVER_TS,
            "ciclo": self.estado["ciclo"],
            "intervalo_seg": self.cfg["intervalo_seg"],
            "offline_total": sum(1 for e in equipos.values() if not e["online"]),
            "criticos_total": len(crit),
            "criticos_offline": sum(1 for c in crit.values() if c["online"] is False),
            "sitios": {
                n: {"activos": sum(1 for e in equipos.values() if e.get("sitio") == n and e["online"]),
                    # solo cuentan los equipos que alguna vez respondieron (el escaneo trae las 254 IPs de la red)
                    "total": sum(1 for e in equipos.values() if e.get("sitio") == n),
                    "sin_datos": bool(s["sin_datos"]),
                    "offline": sum(1 for e in equipos.values() if e.get("sitio") == n and not e["online"]),
                    "cidr": s.get("cidr", ""),
                    "etiqueta": self._etiqueta_sitio(n, s.get("cidr", "")),
                    "ultimo_escaneo_ms": int(s["cambio_ts"] * 1000),
                    "intervalo_sitio_seg": int(self.cfg.get("intervalo_sitio_seg", 120))}
                for n, s in self.estado["sitios"].items()
            },
        }
        try:
            self.fb.put("colector/estado", resumen)
            self.fb.put("colector/criticos", crit or None)
        except FirebaseError as e:
            log.warning("No se pudo publicar el estado: %s", e)

    def _podar_eventos(self):
        if not self.publicar or self.reloj() - self.estado["ultima_poda"] < 3600:
            return
        self.estado["ultima_poda"] = self.reloj()
        try:
            claves = sorted((self.fb.get("colector/eventos", shallow="true") or {}).keys())
            sobran = claves[:-self.cfg["max_eventos"]] if len(claves) > self.cfg["max_eventos"] else []
            for i in range(0, len(sobran), 100):
                self.fb.patch("colector/eventos", {k: None for k in sobran[i:i + 100]})
            if sobran:
                log.info("Eventos viejos borrados: %d", len(sobran))
        except FirebaseError as e:
            log.warning("No se pudo podar colector/eventos: %s", e)

    # ---- ciclo ----------------------------------------------------------
    def ciclo(self):
        self.estado["ciclo"] += 1
        self._refrescar_criticos(forzar=self.estado["ciclo"] == 1 and not self.estado["criticos"].get("ips"))
        try:
            todos = self.fb.get("escaneos_remotos", shallow="true") or {}
        except FirebaseError as e:
            log.warning("No se pudo listar los sitios: %s", e)
            todos = None
        eventos = []
        if todos is not None:
            sitios = self.cfg["sitios"] or sorted(todos.keys())
            for nombre in sitios:
                try:
                    self._procesar_sitio(nombre, eventos)
                except FirebaseError as e:
                    log.warning("Sitio %s: %s", nombre, e)
        self._publicar(eventos)
        self._podar_eventos()
        return eventos


# --------------------------------------------------------------------------
# Arranque
# --------------------------------------------------------------------------
def cargar_config(ruta):
    try:
        crudo = json.loads(Path(ruta).read_text(encoding="utf-8-sig"))
    except OSError:
        sys.exit("Falta %s -- copia colector_config.json.example como colector_config.json y completalo." % ruta)
    except ValueError as e:
        sys.exit("%s no es JSON valido: %s" % (ruta, e))
    cfg = dict(POR_DEFECTO)
    cfg.update({k: v for k, v in crudo.items() if k in POR_DEFECTO})
    cfg["firebase"] = crudo.get("firebase") or {}
    return cfg


def cargar_estado():
    try:
        return json.loads(RUTA_ESTADO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def guardar_estado(estado):
    tmp = RUTA_ESTADO.with_suffix(".tmp")
    tmp.write_text(json.dumps(estado), encoding="utf-8")
    tmp.replace(RUTA_ESTADO)


def main():
    ap = argparse.ArgumentParser(description="NetWatch Colector (vigilante liviano via Firebase)")
    ap.add_argument("--config", default=str(RUTA_CONFIG))
    ap.add_argument("--once", action="store_true", help="un solo ciclo y termina")
    ap.add_argument("--solo-leer", action="store_true",
                    help="prueba: lee Firebase y muestra los cambios, pero no publica nada ni guarda estado")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S",
                        handlers=[RotatingFileHandler(RUTA_LOG, maxBytes=1_000_000, backupCount=3, encoding="utf-8"),
                                  logging.StreamHandler()])
    cfg = cargar_config(args.config)
    try:
        fb = Firebase(cfg["firebase"])
    except FirebaseError as e:
        sys.exit(str(e))
    estado = {} if args.solo_leer else cargar_estado()
    colector = Colector(fb, cfg, estado, publicar=not args.solo_leer)
    log.info("NetWatch Colector v%s iniciado. Ciclo cada %ss%s.", VERSION, cfg["intervalo_seg"],
             " (modo prueba: no publica)" if args.solo_leer else "")

    while True:
        inicio = time.time()
        try:
            colector.ciclo()
            if not args.solo_leer:
                guardar_estado(estado)
        except Exception as e:  # nada debe matar el bucle: se anota y se sigue
            log.exception("Error inesperado en el ciclo (se reintenta): %s", e)
        if args.once or args.solo_leer:
            break
        time.sleep(max(5, cfg["intervalo_seg"] - (time.time() - inicio)))


if __name__ == "__main__":
    main()
