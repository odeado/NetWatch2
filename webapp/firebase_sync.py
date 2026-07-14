"""
Sincronizacion con Firebase Realtime Database (solo datos de "administracion":
empleados y equipos -- nunca estado de escaneo/online-offline, para no gastar
la cuota gratis de Firebase con el monitoreo continuo).

Usa solo la libreria estandar (urllib) para mantenerse en linea con el resto
del proyecto. Requiere un archivo local webapp/firebase_config.json (NO se
sube a git) con:
    {
      "apiKey": "...",
      "databaseURL": "https://tu-proyecto-default-rtdb.firebaseio.com",
      "email": "tu-correo@ejemplo.com",
      "password": "la-contraseña-que-creaste-en-Firebase-Auth"
    }

Direccion de la sincronizacion (boton "Sincronizar con la nube" en Administracion):
  - Empleados/equipos que existen en Firebase pero no localmente -> se crean localmente.
  - Empleados/equipos que existen en ambos lados (match por nombre / IP-hostname)
    -> los campos NO VACIOS que vienen de Firebase pisan los locales (la nube
       manda porque ahi se hicieron las ediciones remotas).
  - Empleados/equipos locales que todavia no tienen firebase_id (creados o
    editados localmente y nunca subidos) -> se suben a Firebase.
"""

import json
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import db

CONFIG_PATH = Path(__file__).resolve().parent / "firebase_config.json"

# Campos de "administracion" que viajan a Firebase. Todo lo que es estado de
# escaneo (en_linea, fallos_consecutivos, estado_deteccion, open_ports, etc.)
# se queda solo local a proposito.
CAMPOS_USUARIO = [
    "nombre", "correo", "cargo", "sucursal", "telefono", "activo",
    "departamento", "ciudad", "lugar_trabajo", "tipo_vpn",
]
CAMPOS_EQUIPO = [
    "hostname", "ip", "mac", "marca", "modelo", "numero_serie",
    "fecha_adquisicion", "garantia_hasta", "responsable", "correo_responsable",
    "sucursal", "ciudad", "departamento", "cpu", "ram", "almacenamiento",
    "gpu", "placa_madre", "estado_ciclo_vida", "critico", "gestionado",
    "notas", "os", "office", "antivirus",
]


class FirebaseConfigError(Exception):
    pass


def _cargar_config():
    if not CONFIG_PATH.exists():
        raise FirebaseConfigError("config_faltante")
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise FirebaseConfigError("config_invalida")
    faltan = [k for k in ("apiKey", "databaseURL", "email", "password") if not cfg.get(k)]
    if faltan:
        raise FirebaseConfigError("config_incompleta")
    cfg["databaseURL"] = cfg["databaseURL"].rstrip("/")
    return cfg


def _clave_nombre(texto):
    sin_acentos = "".join(c for c in unicodedata.normalize("NFD", texto or "") if unicodedata.category(c) != "Mn")
    return " ".join(sin_acentos.strip().lower().split())


def _http_json(url, method="GET", body=None, timeout=15):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                  headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raise FirebaseConfigError(f"http_{e.code}: {e.read().decode('utf-8', 'ignore')[:200]}")
    except urllib.error.URLError as e:
        raise FirebaseConfigError(f"conexion: {e.reason}")
    return json.loads(raw) if raw else None


def _iniciar_sesion(cfg):
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={cfg['apiKey']}"
    resp = _http_json(url, method="POST", body={
        "email": cfg["email"], "password": cfg["password"], "returnSecureToken": True,
    })
    return resp["idToken"]


def _leer_nodo(cfg, nodo, id_token):
    url = f"{cfg['databaseURL']}/{nodo}.json?auth={id_token}"
    data = _http_json(url)
    return data or {}


def _crear_registro(cfg, nodo, id_token, campos):
    url = f"{cfg['databaseURL']}/{nodo}.json?auth={id_token}"
    resp = _http_json(url, method="POST", body=campos)
    return resp["name"]


def _actualizar_registro(cfg, nodo, id_token, firebase_id, campos):
    url = f"{cfg['databaseURL']}/{nodo}/{firebase_id}.json?auth={id_token}"
    _http_json(url, method="PATCH", body=campos)


def _ip_valida(ip):
    partes = (ip or "").split(".")
    if len(partes) != 4:
        return False
    return all(p.isdigit() and 0 <= int(p) <= 255 for p in partes)


def _sincronizar_usuarios(cfg, id_token, conn):
    remotos = _leer_nodo(cfg, "empleados", id_token)
    locales = [dict(r) for r in conn.execute("SELECT * FROM usuarios").fetchall()]
    por_clave = {_clave_nombre(u["nombre"]): u for u in locales}
    por_fbid = {u["firebase_id"]: u for u in locales if u.get("firebase_id")}

    creados = actualizados = 0
    for fb_id, campos in remotos.items():
        if not campos or not campos.get("nombre"):
            continue
        local = por_fbid.get(fb_id) or por_clave.get(_clave_nombre(campos["nombre"]))
        if local:
            updates = {}
            for campo in CAMPOS_USUARIO:
                val = campos.get(campo)
                if val not in (None, "") and val != local.get(campo):
                    updates[campo] = val
            if local.get("firebase_id") != fb_id:
                updates["firebase_id"] = fb_id
            if updates:
                updates["actualizado_en"] = datetime.now().isoformat()
                set_clause = ", ".join(f"{k} = ?" for k in updates)
                conn.execute(f"UPDATE usuarios SET {set_clause} WHERE id = ?",
                             list(updates.values()) + [local["id"]])
                actualizados += 1
                local.update(updates)
        else:
            now = datetime.now().isoformat()
            valores = {c: campos.get(c) for c in CAMPOS_USUARIO}
            valores["nombre"] = campos["nombre"]
            valores["activo"] = 1 if valores.get("activo") in (True, 1, "1", None) else 0
            cur = conn.execute(
                """INSERT INTO usuarios (nombre, correo, cargo, sucursal, telefono, activo,
                       creado_en, departamento, ciudad, lugar_trabajo, tipo_vpn, firebase_id, actualizado_en)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (valores["nombre"], valores.get("correo"), valores.get("cargo"), valores.get("sucursal"),
                 valores.get("telefono"), valores["activo"], now, valores.get("departamento"),
                 valores.get("ciudad"), valores.get("lugar_trabajo") or "Presencial", valores.get("tipo_vpn"),
                 fb_id, now),
            )
            nuevo = dict(conn.execute("SELECT * FROM usuarios WHERE id = ?", (cur.lastrowid,)).fetchone())
            por_clave[_clave_nombre(nuevo["nombre"])] = nuevo
            por_fbid[fb_id] = nuevo
            creados += 1

    subidos = 0
    for u in locales:
        if u.get("firebase_id"):
            continue
        campos = {c: u.get(c) for c in CAMPOS_USUARIO if u.get(c) not in (None, "")}
        if not campos.get("nombre"):
            continue
        fb_id = _crear_registro(cfg, "empleados", id_token, campos)
        conn.execute("UPDATE usuarios SET firebase_id = ? WHERE id = ?", (fb_id, u["id"]))
        subidos += 1

    return {"bajados_nuevos": creados, "actualizados": actualizados, "subidos": subidos}


def _clave_equipo(ip, hostname):
    if _ip_valida(ip):
        return f"ip:{ip}"
    if hostname:
        return f"host:{hostname.strip().lower()}"
    return None


def _sincronizar_equipos(cfg, id_token, conn):
    remotos = _leer_nodo(cfg, "equipos", id_token)
    locales = [dict(r) for r in conn.execute("SELECT * FROM equipos").fetchall()]
    por_clave = {}
    for e in locales:
        k = _clave_equipo(e.get("ip"), e.get("hostname"))
        if k:
            por_clave[k] = e
    por_fbid = {e["firebase_id"]: e for e in locales if e.get("firebase_id")}

    creados = actualizados = 0
    for fb_id, campos in remotos.items():
        if not campos:
            continue
        clave = _clave_equipo(campos.get("ip"), campos.get("hostname"))
        local = por_fbid.get(fb_id) or (por_clave.get(clave) if clave else None)
        if local:
            updates = {}
            for campo in CAMPOS_EQUIPO:
                val = campos.get(campo)
                if val not in (None, "") and val != local.get(campo):
                    updates[campo] = val
            if local.get("firebase_id") != fb_id:
                updates["firebase_id"] = fb_id
            if updates:
                updates["actualizado_en"] = datetime.now().isoformat()
                set_clause = ", ".join(f"{k} = ?" for k in updates)
                conn.execute(f"UPDATE equipos SET {set_clause} WHERE id = ?",
                             list(updates.values()) + [local["id"]])
                actualizados += 1
                local.update(updates)
        else:
            if not clave:
                continue
            ip_final = campos.get("ip") if _ip_valida(campos.get("ip")) else (campos.get("hostname") or "").strip()
            if not ip_final:
                continue
            sufijo = 1
            ip_probar = ip_final
            while conn.execute("SELECT 1 FROM equipos WHERE ip = ?", (ip_probar,)).fetchone():
                sufijo += 1
                ip_probar = f"{ip_final}-{sufijo}"
            now = datetime.now().isoformat()
            campos_ins = {c: campos.get(c) for c in CAMPOS_EQUIPO if c not in ("ip", "hostname")}
            cur = conn.execute(
                """INSERT INTO equipos (ip, hostname, mac, marca, modelo, numero_serie,
                       fecha_adquisicion, garantia_hasta, responsable, correo_responsable,
                       sucursal, ciudad, departamento, cpu, ram, almacenamiento, gpu,
                       placa_madre, estado_ciclo_vida, critico, gestionado, notas, os,
                       office, antivirus, origen, primera_deteccion, firebase_id, actualizado_en)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (ip_probar, campos.get("hostname"), campos_ins.get("mac"), campos_ins.get("marca"),
                 campos_ins.get("modelo"), campos_ins.get("numero_serie"), campos_ins.get("fecha_adquisicion"),
                 campos_ins.get("garantia_hasta"), campos_ins.get("responsable"), campos_ins.get("correo_responsable"),
                 campos_ins.get("sucursal"), campos_ins.get("ciudad"), campos_ins.get("departamento"),
                 campos_ins.get("cpu"), campos_ins.get("ram"), campos_ins.get("almacenamiento"),
                 campos_ins.get("gpu"), campos_ins.get("placa_madre"),
                 campos_ins.get("estado_ciclo_vida") or "activo", 1 if campos_ins.get("critico") else 0,
                 1 if campos_ins.get("gestionado") else 0, campos_ins.get("notas"), campos_ins.get("os"),
                 campos_ins.get("office"), campos_ins.get("antivirus"), "manual", now, fb_id, now),
            )
            nuevo = dict(conn.execute("SELECT * FROM equipos WHERE id = ?", (cur.lastrowid,)).fetchone())
            nueva_clave = _clave_equipo(nuevo.get("ip"), nuevo.get("hostname"))
            if nueva_clave:
                por_clave[nueva_clave] = nuevo
            por_fbid[fb_id] = nuevo
            creados += 1

    subidos = 0
    for e in locales:
        if e.get("firebase_id"):
            continue
        campos = {c: e.get(c) for c in CAMPOS_EQUIPO if e.get(c) not in (None, "")}
        if not campos.get("hostname") and not campos.get("ip"):
            continue
        fb_id = _crear_registro(cfg, "equipos", id_token, campos)
        conn.execute("UPDATE equipos SET firebase_id = ? WHERE id = ?", (fb_id, e["id"]))
        subidos += 1

    return {"bajados_nuevos": creados, "actualizados": actualizados, "subidos": subidos}


def sincronizar():
    """Punto de entrada usado por la ruta Flask. Nunca lanza excepciones de
    red hacia afuera -- las devuelve envueltas en {"error": "..."}."""
    try:
        cfg = _cargar_config()
        id_token = _iniciar_sesion(cfg)
    except FirebaseConfigError as e:
        return {"error": str(e)}

    conn = db.get_connection()
    try:
        resumen_usuarios = _sincronizar_usuarios(cfg, id_token, conn)
        resumen_equipos = _sincronizar_equipos(cfg, id_token, conn)
        conn.commit()
    except FirebaseConfigError as e:
        conn.rollback()
        return {"error": str(e)}
    finally:
        conn.close()

    return {"usuarios": resumen_usuarios, "equipos": resumen_equipos}
