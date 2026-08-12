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
  - Empleados/equipos/dispositivos que existen en Firebase pero no localmente
    -> se crean localmente.
  - Empleados/equipos/dispositivos que existen en ambos lados (match por
    nombre / IP-hostname) -> gana el lado que se edito MAS RECIENTE (columna
    actualizado_en de cada lado, decision explicita de Andres 2026-07-27: "el
    ultimo registro debe mandar, ya que pudo cambiar en web o en local"). Si
    el registro local no tiene actualizado_en (dato viejo, de antes de que
    existiera este campo), se trata como el mas antiguo posible y la nube
    gana por defecto, igual que el comportamiento previo a este cambio.
  - Empleados/equipos/dispositivos locales que todavia no tienen firebase_id
    (creados o editados localmente y nunca subidos) -> se suben a Firebase.
"""

import json
import threading
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import db

CONFIG_PATH = Path(__file__).resolve().parent / "firebase_config.json"


def _parsear_ts(valor):
    """Convierte un actualizado_en (ISO 8601, con o sin 'Z'/offset) en un
    datetime timezone-aware (UTC) para poder compararlo. Devuelve None si no
    hay valor o no se pudo parsear (dato viejo o formato raro -- se trata
    como "sin timestamp", el caso mas antiguo posible)."""
    if not valor or not isinstance(valor, str):
        return None
    v = valor.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    if dt.tzinfo is None:
        # Timestamp viejo guardado en hora local antes de este cambio (o el
        # agente/algun llamador que no mando con offset) -- se asume UTC. No
        # es perfecto si el servidor no esta en UTC, pero es mejor que
        # reventar la comparacion, y estos casos van desapareciendo solos a
        # medida que se re-guardan los registros.
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _remoto_gana(local, campos_remotos):
    """Decide quien manda para ESTE registro puntual cuando existe en ambos
    lados: True si Firebase se edito mas reciente que el local (o el local
    nunca quedo con timestamp -- dato viejo, se preserva el comportamiento de
    antes de este cambio y gana la nube). False si el local es igual o mas
    reciente -- en ese caso no se aplica NINGUN campo de Firebase esta vuelta,
    para no pisar una edicion que se acaba de hacer a mano."""
    ts_local = _parsear_ts(local.get("actualizado_en"))
    if ts_local is None:
        return True
    ts_remoto = _parsear_ts(campos_remotos.get("actualizado_en"))
    if ts_remoto is None:
        return False
    return ts_remoto > ts_local


def _subir_diferencias_locales(cfg, id_token, nodo, fb_id, local, campos_remotos, lista_campos):
    """Cuando el LOCAL gana el registro (ver _remoto_gana), empuja a Firebase
    los campos donde el local difiere de lo que hay ahi -- si no, un equipo
    ya vinculado (con firebase_id, o sea que se subio una vez hace tiempo)
    nunca volvia a reflejar sus ediciones locales posteriores en la nube: el
    codigo viejo solo subia registros COMPLETAMENTE nuevos (sin firebase_id),
    y una vez vinculado, "Sincronizar con la nube" solo miraba
    nube-hacia-local, nunca al reves. Resultado real (caso Andres
    2026-08-12): edito un equipo local, agrego responsable, sincronizo, y la
    web-admin siguio mostrando los datos viejos -- 0 actualizados, 0 subidos.
    Devuelve True si empujo algo."""
    cambios = {}
    for campo in lista_campos:
        val_local = local.get(campo)
        if val_local in (None, ""):
            continue
        if val_local != campos_remotos.get(campo):
            cambios[campo] = val_local
    if not cambios:
        return False
    cambios["actualizado_en"] = local.get("actualizado_en") or datetime.now(timezone.utc).isoformat()
    _actualizar_registro(cfg, nodo, id_token, fb_id, cambios)
    return True

# --- estado compartido para la barra de progreso ---------------------------
_estado_lock = threading.Lock()
_estado = {"corriendo": False, "total": 0, "procesados": 0, "fase": "inactivo", "resumen": None, "error": None}


def obtener_estado():
    with _estado_lock:
        return dict(_estado)


def _actualizar_estado(**kwargs):
    with _estado_lock:
        _estado.update(kwargs)


def _incrementar_progreso(n=1):
    with _estado_lock:
        _estado["procesados"] += n


def iniciar_sincronizacion_async():
    """Arranca la sincronizacion en un hilo aparte. Devuelve False si ya
    habia una corriendo (para no pisarla con dos a la vez)."""
    with _estado_lock:
        if _estado["corriendo"]:
            return False
        _estado.update(corriendo=True, total=0, procesados=0, fase="conectando", resumen=None, error=None)
    hilo = threading.Thread(target=_ejecutar_en_hilo, daemon=True)
    hilo.start()
    return True


def _ejecutar_en_hilo():
    resultado = sincronizar()
    if "error" in resultado:
        _actualizar_estado(corriendo=False, fase="error", error=resultado["error"])
    else:
        _actualizar_estado(corriendo=False, fase="listo", resumen=resultado)

# Campos de "administracion" que viajan a Firebase. Todo lo que es estado de
# escaneo (en_linea, fallos_consecutivos, estado_deteccion, open_ports, etc.)
# se queda solo local a proposito.
CAMPOS_USUARIO = [
    "nombre", "correo", "cargo", "sucursal", "telefono", "activo",
    "departamento", "ciudad", "lugar_trabajo", "tipo_vpn", "vpn_activa",
    "sistemas_autorizados",
]
CAMPOS_EQUIPO = [
    "hostname", "ip", "mac", "marca", "modelo", "numero_serie",
    "fecha_adquisicion", "garantia_hasta", "responsable", "correo_responsable",
    "sucursal", "ciudad", "departamento", "cpu", "ram", "almacenamiento",
    "gpu", "placa_madre", "estado_ciclo_vida", "critico", "gestionado",
    "notas", "os", "office", "antivirus", "puerto",
]
# Campo especial (no es columna de equipos): el nombre del switch/router al
# que esta conectado, para resolverlo a dispositivo_id via el catalogo de
# dispositivos ya sincronizado.
CAMPO_EQUIPO_DISPOSITIVO = "dispositivo_nombre"

CAMPOS_DISPOSITIVO = [
    "nombre", "tipo", "marca", "modelo", "numero_serie", "cantidad_bocas",
    "bocas_fibra", "ip", "mac", "sucursal", "ciudad", "ubicacion", "piso",
    "estado", "fecha_ingreso", "enlace", "notas",
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
    except OSError as e:
        # Mismo bug que en scanner/firebase_push.py: un corte de conexion a
        # mitad de respuesta (ej. http.client.RemoteDisconnected) no es un
        # URLError, asi que se colaba sin control y podia matar monitor.py
        # entero (que llama esto cada ciclo para leer los sitios remotos).
        raise FirebaseConfigError(f"conexion: {e}")
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


def _sincronizar_usuarios(cfg, id_token, conn, remotos, locales, on_progreso=None):
    pendientes_subir = {u["id"] for u in locales if not u.get("firebase_id")}
    por_clave = {_clave_nombre(u["nombre"]): u for u in locales}
    por_fbid = {u["firebase_id"]: u for u in locales if u.get("firebase_id")}

    creados = actualizados = subidos = 0
    for fb_id, campos in remotos.items():
        if on_progreso:
            on_progreso()
        if not campos or not campos.get("nombre"):
            continue
        local = por_fbid.get(fb_id) or por_clave.get(_clave_nombre(campos["nombre"]))
        if local:
            gana_remoto = _remoto_gana(local, campos)
            updates = {}
            if gana_remoto:
                for campo in CAMPOS_USUARIO:
                    val = campos.get(campo)
                    if val not in (None, "") and val != local.get(campo):
                        updates[campo] = val
            elif local.get("firebase_id") == fb_id:
                # El local gana y ya esta vinculado a este registro de
                # Firebase -- empujar la version local hacia arriba, si no
                # la nube/web-admin se queda mostrando datos viejos aunque
                # "Sincronizar con la nube" diga que no hubo diferencias.
                if _subir_diferencias_locales(cfg, id_token, "empleados", fb_id, local, campos, CAMPOS_USUARIO):
                    subidos += 1
            if local.get("firebase_id") != fb_id:
                updates["firebase_id"] = fb_id
            if updates:
                if any(k != "firebase_id" for k in updates):
                    updates["actualizado_en"] = campos.get("actualizado_en") or datetime.now(timezone.utc).isoformat()
                set_clause = ", ".join(f"{k} = ?" for k in updates)
                conn.execute(f"UPDATE usuarios SET {set_clause} WHERE id = ?",
                             list(updates.values()) + [local["id"]])
                actualizados += 1
                local.update(updates)
        else:
            now = datetime.now().isoformat()
            marca_ts = campos.get("actualizado_en") or datetime.now(timezone.utc).isoformat()
            valores = {c: campos.get(c) for c in CAMPOS_USUARIO}
            valores["nombre"] = campos["nombre"]
            valores["activo"] = 1 if valores.get("activo") in (True, 1, "1", None) else 0
            valores["vpn_activa"] = 1 if valores.get("vpn_activa") in (True, 1, "1") else 0
            cur = conn.execute(
                """INSERT INTO usuarios (nombre, correo, cargo, sucursal, telefono, activo,
                       creado_en, departamento, ciudad, lugar_trabajo, tipo_vpn, vpn_activa,
                       sistemas_autorizados, firebase_id, actualizado_en)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (valores["nombre"], valores.get("correo"), valores.get("cargo"), valores.get("sucursal"),
                 valores.get("telefono"), valores["activo"], now, valores.get("departamento"),
                 valores.get("ciudad"), valores.get("lugar_trabajo") or "Presencial", valores.get("tipo_vpn"),
                 valores["vpn_activa"], valores.get("sistemas_autorizados"), fb_id, marca_ts),
            )
            nuevo = dict(conn.execute("SELECT * FROM usuarios WHERE id = ?", (cur.lastrowid,)).fetchone())
            por_clave[_clave_nombre(nuevo["nombre"])] = nuevo
            por_fbid[fb_id] = nuevo
            creados += 1

    for u in locales:
        if u["id"] not in pendientes_subir:
            continue
        if on_progreso:
            on_progreso()
        if u.get("firebase_id"):
            continue  # ya quedo enlazado al hacer el match en el paso de arriba
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


def _sincronizar_equipos(cfg, id_token, conn, remotos, locales, on_progreso=None,
                          dispositivos_por_clave=None, dispositivos_por_id=None):
    dispositivos_por_clave = dispositivos_por_clave or {}
    dispositivos_por_id = dispositivos_por_id or {}
    pendientes_subir = {e["id"] for e in locales if not e.get("firebase_id")}
    por_clave = {}
    for e in locales:
        k = _clave_equipo(e.get("ip"), e.get("hostname"))
        if k:
            por_clave[k] = e
    por_fbid = {e["firebase_id"]: e for e in locales if e.get("firebase_id")}

    creados = actualizados = subidos = 0
    for fb_id, campos in remotos.items():
        if on_progreso:
            on_progreso()
        if not campos:
            continue
        clave = _clave_equipo(campos.get("ip"), campos.get("hostname"))
        local = por_fbid.get(fb_id) or (por_clave.get(clave) if clave else None)
        if local:
            # Gana el mas reciente (columna actualizado_en de cada lado) para
            # todo el registro -- decision explicita de Andres: "el ultimo
            # registro debe mandar, ya que pudo cambiar en web o en local".
            # Si el local es igual o mas nuevo, NO se aplica ningun campo de
            # Firebase esta vuelta (para no pisar una edicion recien hecha a
            # mano en el sistema local).
            gana_remoto = _remoto_gana(local, campos)
            updates = {}
            for campo in CAMPOS_EQUIPO:
                val = campos.get(campo)
                if campo in ("critico", "gestionado"):
                    # Banderas booleanas: esta regla es APARTE del "gana el
                    # mas reciente" de arriba y siempre se aplica, sin
                    # importar quien gano el registro -- OJO -- antes se
                    # trataban igual que cualquier otro campo, y "val not in
                    # (None, '')" deja pasar un 0 (0 no es None ni ""), asi
                    # que si la nube tenia critico=0 (el default de cualquier
                    # equipo que nunca se marco critico desde el celular),
                    # cada "Sincronizar con la nube" pisaba el 1 marcado en
                    # el sistema local y el equipo dejaba de avisar por
                    # WhatsApp sin que nadie lo apagara a mano. Ahora la nube
                    # solo puede PRENDER la bandera, nunca apagarla via sync
                    # -- para apagarla se usa el boton Critico/Descartar en
                    # el sistema local.
                    if val and not local.get(campo):
                        updates[campo] = 1
                    continue
                if not gana_remoto:
                    continue
                if val not in (None, "") and val != local.get(campo):
                    updates[campo] = val
            nombre_disp = campos.get(CAMPO_EQUIPO_DISPOSITIVO)
            if gana_remoto and nombre_disp:
                disp_id = dispositivos_por_clave.get(_clave_nombre(nombre_disp))
                if disp_id and disp_id != local.get("dispositivo_id"):
                    updates["dispositivo_id"] = disp_id
            if not gana_remoto and local.get("firebase_id") == fb_id:
                # El local gana y ya esta vinculado a este registro de
                # Firebase -- empujar la version local hacia arriba (incluye
                # critico/gestionado en su estado real, y el nombre del
                # switch/dispositivo conectado). Sin esto un equipo ya
                # subido una vez nunca reflejaba ediciones locales
                # posteriores en la nube/web-admin.
                local_con_disp = local
                if local.get("dispositivo_id") and local["dispositivo_id"] in dispositivos_por_id:
                    local_con_disp = dict(local)
                    local_con_disp[CAMPO_EQUIPO_DISPOSITIVO] = dispositivos_por_id[local["dispositivo_id"]]
                if _subir_diferencias_locales(cfg, id_token, "equipos", fb_id, local_con_disp, campos,
                                               CAMPOS_EQUIPO + [CAMPO_EQUIPO_DISPOSITIVO]):
                    subidos += 1
            if local.get("firebase_id") != fb_id:
                updates["firebase_id"] = fb_id
            if updates:
                if any(k != "firebase_id" for k in updates):
                    updates["actualizado_en"] = campos.get("actualizado_en") or datetime.now(timezone.utc).isoformat()
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
            marca_ts = campos.get("actualizado_en") or datetime.now(timezone.utc).isoformat()
            campos_ins = {c: campos.get(c) for c in CAMPOS_EQUIPO if c not in ("ip", "hostname")}
            nombre_disp = campos.get(CAMPO_EQUIPO_DISPOSITIVO)
            dispositivo_id = dispositivos_por_clave.get(_clave_nombre(nombre_disp)) if nombre_disp else None
            cur = conn.execute(
                """INSERT INTO equipos (ip, hostname, mac, marca, modelo, numero_serie,
                       fecha_adquisicion, garantia_hasta, responsable, correo_responsable,
                       sucursal, ciudad, departamento, cpu, ram, almacenamiento, gpu,
                       placa_madre, estado_ciclo_vida, critico, gestionado, notas, os,
                       office, antivirus, puerto, dispositivo_id, origen, primera_deteccion,
                       firebase_id, actualizado_en)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (ip_probar, campos.get("hostname"), campos_ins.get("mac"), campos_ins.get("marca"),
                 campos_ins.get("modelo"), campos_ins.get("numero_serie"), campos_ins.get("fecha_adquisicion"),
                 campos_ins.get("garantia_hasta"), campos_ins.get("responsable"), campos_ins.get("correo_responsable"),
                 campos_ins.get("sucursal"), campos_ins.get("ciudad"), campos_ins.get("departamento"),
                 campos_ins.get("cpu"), campos_ins.get("ram"), campos_ins.get("almacenamiento"),
                 campos_ins.get("gpu"), campos_ins.get("placa_madre"),
                 campos_ins.get("estado_ciclo_vida") or "activo", 1 if campos_ins.get("critico") else 0,
                 1 if campos_ins.get("gestionado") else 0, campos_ins.get("notas"), campos_ins.get("os"),
                 campos_ins.get("office"), campos_ins.get("antivirus"), campos_ins.get("puerto"),
                 dispositivo_id, "manual", now, fb_id, marca_ts),
            )
            nuevo = dict(conn.execute("SELECT * FROM equipos WHERE id = ?", (cur.lastrowid,)).fetchone())
            nueva_clave = _clave_equipo(nuevo.get("ip"), nuevo.get("hostname"))
            if nueva_clave:
                por_clave[nueva_clave] = nuevo
            por_fbid[fb_id] = nuevo
            creados += 1

    for e in locales:
        if e["id"] not in pendientes_subir:
            continue
        if on_progreso:
            on_progreso()
        if e.get("firebase_id"):
            continue  # ya quedo enlazado al hacer el match en el paso de arriba
        campos = {c: e.get(c) for c in CAMPOS_EQUIPO if e.get(c) not in (None, "")}
        if e.get("dispositivo_id") and e["dispositivo_id"] in dispositivos_por_id:
            campos[CAMPO_EQUIPO_DISPOSITIVO] = dispositivos_por_id[e["dispositivo_id"]]
        if not campos.get("hostname") and not campos.get("ip"):
            continue
        fb_id = _crear_registro(cfg, "equipos", id_token, campos)
        conn.execute("UPDATE equipos SET firebase_id = ? WHERE id = ?", (fb_id, e["id"]))
        subidos += 1

    return {"bajados_nuevos": creados, "actualizados": actualizados, "subidos": subidos}


def _sincronizar_dispositivos(cfg, id_token, conn, remotos, locales, on_progreso=None):
    """Switches/routers (dispositivos_red). Match por nombre (sin acentos),
    igual que usuarios -- no tienen IP unica garantizada."""
    pendientes_subir = {d["id"] for d in locales if not d.get("firebase_id")}
    por_clave = {_clave_nombre(d["nombre"]): d for d in locales}
    por_fbid = {d["firebase_id"]: d for d in locales if d.get("firebase_id")}

    creados = actualizados = subidos = 0
    for fb_id, campos in remotos.items():
        if on_progreso:
            on_progreso()
        if not campos or not campos.get("nombre"):
            continue
        local = por_fbid.get(fb_id) or por_clave.get(_clave_nombre(campos["nombre"]))
        if local:
            gana_remoto = _remoto_gana(local, campos)
            updates = {}
            if gana_remoto:
                for campo in CAMPOS_DISPOSITIVO:
                    val = campos.get(campo)
                    if val not in (None, "") and val != local.get(campo):
                        updates[campo] = val
            elif local.get("firebase_id") == fb_id:
                if _subir_diferencias_locales(cfg, id_token, "dispositivos", fb_id, local, campos, CAMPOS_DISPOSITIVO):
                    subidos += 1
            if local.get("firebase_id") != fb_id:
                updates["firebase_id"] = fb_id
            if updates:
                if any(k != "firebase_id" for k in updates):
                    updates["actualizado_en"] = campos.get("actualizado_en") or datetime.now(timezone.utc).isoformat()
                set_clause = ", ".join(f"{k} = ?" for k in updates)
                conn.execute(f"UPDATE dispositivos_red SET {set_clause} WHERE id = ?",
                             list(updates.values()) + [local["id"]])
                actualizados += 1
                local.update(updates)
        else:
            now = datetime.now().isoformat()
            marca_ts = campos.get("actualizado_en") or datetime.now(timezone.utc).isoformat()
            cur = conn.execute(
                """INSERT INTO dispositivos_red (
                       nombre, tipo, marca, modelo, numero_serie, cantidad_bocas, bocas_fibra,
                       ip, mac, sucursal, ciudad, ubicacion, piso, estado, fecha_ingreso, enlace,
                       notas, creado_en, firebase_id, actualizado_en
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (campos["nombre"], campos.get("tipo") or "switch", campos.get("marca"), campos.get("modelo"),
                 campos.get("numero_serie"), campos.get("cantidad_bocas"), campos.get("bocas_fibra"),
                 campos.get("ip"), campos.get("mac"), campos.get("sucursal"), campos.get("ciudad"),
                 campos.get("ubicacion"), campos.get("piso"), campos.get("estado") or "Usado",
                 campos.get("fecha_ingreso"), campos.get("enlace"), campos.get("notas"), now, fb_id, marca_ts),
            )
            nuevo = dict(conn.execute("SELECT * FROM dispositivos_red WHERE id = ?", (cur.lastrowid,)).fetchone())
            por_clave[_clave_nombre(nuevo["nombre"])] = nuevo
            por_fbid[fb_id] = nuevo
            creados += 1

    for d in locales:
        if d["id"] not in pendientes_subir:
            continue
        if on_progreso:
            on_progreso()
        if d.get("firebase_id"):
            continue
        campos = {c: d.get(c) for c in CAMPOS_DISPOSITIVO if d.get(c) not in (None, "")}
        if not campos.get("nombre"):
            continue
        fb_id = _crear_registro(cfg, "dispositivos", id_token, campos)
        conn.execute("UPDATE dispositivos_red SET firebase_id = ? WHERE id = ?", (fb_id, d["id"]))
        subidos += 1

    return {"bajados_nuevos": creados, "actualizados": actualizados, "subidos": subidos}


def _sincronizar_catalogo(cfg, id_token, conn, tabla, nodo, remotos, locales_nombres, on_progreso=None):
    """Catalogos simples (departamentos, ciudades): son solo una lista de
    nombres unicos, asi que el sync es una union de conjuntos en ambas
    direcciones -- no hace falta firebase_id."""
    locales_clave = {_clave_nombre(n): n for n in locales_nombres}
    remotos_nombres = [v for v in remotos.values() if v] if isinstance(remotos, dict) else []
    remotas_claves = {_clave_nombre(n) for n in remotos_nombres}

    creados = 0
    for nombre in remotos_nombres:
        if on_progreso:
            on_progreso()
        clave = _clave_nombre(nombre)
        if clave in locales_clave:
            continue
        try:
            conn.execute(f"INSERT INTO {tabla} (nombre) VALUES (?)", (nombre,))
        except Exception:
            continue
        locales_clave[clave] = nombre
        creados += 1

    subidos = 0
    for nombre in locales_nombres:
        if on_progreso:
            on_progreso()
        if _clave_nombre(nombre) in remotas_claves:
            continue
        _crear_registro(cfg, nodo, id_token, nombre)
        subidos += 1

    return {"bajados_nuevos": creados, "subidos": subidos}


def leer_escaneo_remoto(sitio):
    """Lee el ultimo escaneo publicado por un sitio remoto sin VPN permanente
    (ver scanner/firebase_push.py). Se usa desde monitor.py, no desde la
    ruta de sincronizacion de administracion. Devuelve None si falta la
    configuracion, no hay internet, o el sitio todavia no ha publicado nada."""
    try:
        cfg = _cargar_config()
        id_token = _iniciar_sesion(cfg)
        data = _leer_nodo(cfg, f"escaneos_remotos/{sitio}", id_token)
    except FirebaseConfigError:
        return None
    return data or None


def sincronizar():
    """Punto de entrada usado por la ruta Flask. Nunca lanza excepciones de
    red hacia afuera -- las devuelve envueltas en {"error": "..."}. Va
    reportando progreso en el estado compartido (ver obtener_estado) para que
    la barra de progreso del navegador pueda ir haciendo poll."""
    try:
        _actualizar_estado(fase="conectando")
        cfg = _cargar_config()
        id_token = _iniciar_sesion(cfg)
    except FirebaseConfigError as e:
        _actualizar_estado(error=str(e))
        return {"error": str(e)}

    conn = db.get_connection()
    try:
        _actualizar_estado(fase="leyendo_firebase")
        remotos_usuarios = _leer_nodo(cfg, "empleados", id_token)
        remotos_equipos = _leer_nodo(cfg, "equipos", id_token)
        remotos_dispositivos = _leer_nodo(cfg, "dispositivos", id_token)
        remotos_departamentos = _leer_nodo(cfg, "departamentos", id_token)
        remotos_ciudades = _leer_nodo(cfg, "ciudades", id_token)

        locales_usuarios = [dict(r) for r in conn.execute("SELECT * FROM usuarios").fetchall()]
        locales_equipos = [dict(r) for r in conn.execute("SELECT * FROM equipos").fetchall()]
        locales_dispositivos = [dict(r) for r in conn.execute("SELECT * FROM dispositivos_red").fetchall()]
        locales_departamentos = [r["nombre"] for r in conn.execute("SELECT nombre FROM departamentos").fetchall()]
        locales_ciudades = [r["nombre"] for r in conn.execute("SELECT nombre FROM ciudades").fetchall()]

        total = (
            len(remotos_usuarios) + len(remotos_equipos) + len(remotos_dispositivos)
            + len(remotos_departamentos) + len(remotos_ciudades)
            + sum(1 for u in locales_usuarios if not u.get("firebase_id"))
            + sum(1 for e in locales_equipos if not e.get("firebase_id"))
            + sum(1 for d in locales_dispositivos if not d.get("firebase_id"))
            + len(locales_departamentos) + len(locales_ciudades)
        )
        _actualizar_estado(total=total, procesados=0, fase="sincronizando")

        # dispositivos primero: los equipos necesitan resolver "conectado a que switch"
        resumen_dispositivos = _sincronizar_dispositivos(
            cfg, id_token, conn, remotos_dispositivos, locales_dispositivos, on_progreso=_incrementar_progreso)
        dispositivos_actualizados = [dict(r) for r in conn.execute("SELECT * FROM dispositivos_red").fetchall()]
        dispositivos_por_clave = {_clave_nombre(d["nombre"]): d["id"] for d in dispositivos_actualizados}
        dispositivos_por_id = {d["id"]: d["nombre"] for d in dispositivos_actualizados}

        resumen_usuarios = _sincronizar_usuarios(
            cfg, id_token, conn, remotos_usuarios, locales_usuarios, on_progreso=_incrementar_progreso)
        resumen_equipos = _sincronizar_equipos(
            cfg, id_token, conn, remotos_equipos, locales_equipos, on_progreso=_incrementar_progreso,
            dispositivos_por_clave=dispositivos_por_clave, dispositivos_por_id=dispositivos_por_id)
        resumen_departamentos = _sincronizar_catalogo(
            cfg, id_token, conn, "departamentos", "departamentos", remotos_departamentos,
            locales_departamentos, on_progreso=_incrementar_progreso)
        resumen_ciudades = _sincronizar_catalogo(
            cfg, id_token, conn, "ciudades", "ciudades", remotos_ciudades,
            locales_ciudades, on_progreso=_incrementar_progreso)
        conn.commit()
    except FirebaseConfigError as e:
        conn.rollback()
        _actualizar_estado(error=str(e))
        return {"error": str(e)}
    finally:
        conn.close()

    return {
        "usuarios": resumen_usuarios,
        "equipos": resumen_equipos,
        "dispositivos": resumen_dispositivos,
        "departamentos": resumen_departamentos,
        "ciudades": resumen_ciudades,
    }
