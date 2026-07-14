#!/usr/bin/env python3
"""
Win NetWatch RMM - Capa de datos (SQLite)
==========================================
Base de datos local del inventario de equipos: estado en linea/fuera de
linea, eventos (online/offline/nuevo), ficha tecnica/administrativa,
tickets de soporte por equipo, e historial de conexiones RDP.

100% libreria estandar (sqlite3 viene con Python).
"""

import json
import re
import sqlite3
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "netwatch.db"


def _clave_nombre(nombre):
    """Normaliza un nombre para matchear sin importar mayusculas, espacios
    de mas NI acentos ('Carlos Rodriguez' == 'Carlos Rodríguez'). Antes el
    match de importar_gestion_masiva solo ignoraba mayusculas/espacios, asi
    que un archivo externo sin tildes creaba un usuario duplicado en vez de
    completar el que ya existia con tilde -- confirmado en vivo con 'Carlos
    Rodriguez'/'Carlos Rodríguez' y 'Victor Toloza'/'Víctor Toloza'."""
    sin_acentos = "".join(c for c in unicodedata.normalize("NFD", nombre or "") if unicodedata.category(c) != "Mn")
    return " ".join(sin_acentos.strip().lower().split())

SCHEMA = """
CREATE TABLE IF NOT EXISTS equipos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip TEXT UNIQUE NOT NULL,
    hostname TEXT,
    mac TEXT,
    subred TEXT,
    open_ports TEXT,
    confidence_score INTEGER,
    confidence_label TEXT,
    estado_deteccion TEXT DEFAULT 'pendiente',
    en_linea INTEGER DEFAULT 1,
    fallos_consecutivos INTEGER DEFAULT 0,
    alerta_offline_enviada INTEGER DEFAULT 0,
    desde TEXT,
    primera_deteccion TEXT,
    ultima_deteccion TEXT,
    ultimo_scan_file TEXT,
    origen TEXT DEFAULT 'scanner',

    marca TEXT,
    modelo TEXT,
    numero_serie TEXT,
    fecha_adquisicion TEXT,
    garantia_hasta TEXT,
    responsable TEXT,
    correo_responsable TEXT,
    sucursal TEXT,
    ciudad TEXT,
    departamento TEXT,
    cpu TEXT,
    ram TEXT,
    almacenamiento TEXT,
    gpu TEXT,
    placa_madre TEXT,
    estado_ciclo_vida TEXT DEFAULT 'activo',
    critico INTEGER DEFAULT 0,
    gestionado INTEGER DEFAULT 0,
    notas TEXT,
    os TEXT,
    office TEXT,
    antivirus TEXT
);

CREATE TABLE IF NOT EXISTS eventos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    equipo_id INTEGER,
    ip TEXT,
    hostname TEXT,
    tipo TEXT,
    ts TEXT
);

CREATE TABLE IF NOT EXISTS rdp_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    equipo_id INTEGER,
    ip TEXT,
    hostname TEXT,
    origen_ip TEXT,
    ts TEXT
);

CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    equipo_id INTEGER NOT NULL,
    titulo TEXT NOT NULL,
    descripcion TEXT,
    prioridad TEXT DEFAULT 'normal',
    estado TEXT DEFAULT 'abierto',
    asignado_a TEXT,
    creado_en TEXT,
    actualizado_en TEXT,
    resuelto_en TEXT
);

CREATE TABLE IF NOT EXISTS usuarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    correo TEXT,
    cargo TEXT,
    sucursal TEXT,
    telefono TEXT,
    activo INTEGER DEFAULT 1,
    creado_en TEXT,
    foto_perfil TEXT,
    departamento TEXT,
    ciudad TEXT,
    lugar_trabajo TEXT DEFAULT 'Presencial',
    sistemas_autorizados TEXT,
    tipo_vpn TEXT,
    vpn_activa INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS departamentos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS ciudades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS dispositivos_red (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    tipo TEXT DEFAULT 'switch',
    marca TEXT,
    modelo TEXT,
    numero_serie TEXT,
    cantidad_bocas INTEGER,
    bocas_fibra INTEGER,
    plantilla TEXT DEFAULT 'generico',
    ip TEXT,
    mac TEXT,
    sucursal TEXT,
    ciudad TEXT,
    ubicacion TEXT,
    piso TEXT,
    estado TEXT DEFAULT 'Usado',
    fecha_ingreso TEXT,
    enlace TEXT,
    notas TEXT,
    creado_en TEXT
);

CREATE TABLE IF NOT EXISTS conexiones_dispositivos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dispositivo_id INTEGER NOT NULL,
    puerto TEXT NOT NULL,
    destino_dispositivo_id INTEGER NOT NULL,
    ts TEXT,
    UNIQUE(dispositivo_id, puerto)
);
"""

FICHA_FIELDS = [
    "hostname", "mac",
    "marca", "modelo", "numero_serie", "fecha_adquisicion", "garantia_hasta",
    "responsable", "correo_responsable", "sucursal", "ciudad", "departamento",
    "cpu", "ram", "almacenamiento", "gpu", "placa_madre",
    "estado_ciclo_vida", "notas", "puerto",
    "os", "office", "antivirus",
]


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    conn.executescript(SCHEMA)
    # migracion suave para bases de datos creadas antes de agregar en_linea/desde
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(equipos)")}
    if "en_linea" not in cols:
        conn.execute("ALTER TABLE equipos ADD COLUMN en_linea INTEGER DEFAULT 1")
    if "fallos_consecutivos" not in cols:
        conn.execute("ALTER TABLE equipos ADD COLUMN fallos_consecutivos INTEGER DEFAULT 0")
    if "alerta_offline_enviada" not in cols:
        conn.execute("ALTER TABLE equipos ADD COLUMN alerta_offline_enviada INTEGER DEFAULT 0")
    if "desde" not in cols:
        conn.execute("ALTER TABLE equipos ADD COLUMN desde TEXT")
    if "responsable_id" not in cols:
        conn.execute("ALTER TABLE equipos ADD COLUMN responsable_id INTEGER")
    if "dispositivo_id" not in cols:
        conn.execute("ALTER TABLE equipos ADD COLUMN dispositivo_id INTEGER")
    if "puerto" not in cols:
        conn.execute("ALTER TABLE equipos ADD COLUMN puerto TEXT")
    if "origen" not in cols:
        conn.execute("ALTER TABLE equipos ADD COLUMN origen TEXT DEFAULT 'scanner'")
    if "os" not in cols:
        conn.execute("ALTER TABLE equipos ADD COLUMN os TEXT")
    if "office" not in cols:
        conn.execute("ALTER TABLE equipos ADD COLUMN office TEXT")
    if "antivirus" not in cols:
        conn.execute("ALTER TABLE equipos ADD COLUMN antivirus TEXT")
    if "firebase_id" not in cols:
        conn.execute("ALTER TABLE equipos ADD COLUMN firebase_id TEXT")
    if "actualizado_en" not in cols:
        conn.execute("ALTER TABLE equipos ADD COLUMN actualizado_en TEXT")
    if "metodo_deteccion" not in cols:
        conn.execute("ALTER TABLE equipos ADD COLUMN metodo_deteccion TEXT")
    disp_cols = {row["name"] for row in conn.execute("PRAGMA table_info(dispositivos_red)")}
    if disp_cols:
        if "marca" not in disp_cols:
            conn.execute("ALTER TABLE dispositivos_red ADD COLUMN marca TEXT")
        if "modelo" not in disp_cols:
            conn.execute("ALTER TABLE dispositivos_red ADD COLUMN modelo TEXT")
        if "numero_serie" not in disp_cols:
            conn.execute("ALTER TABLE dispositivos_red ADD COLUMN numero_serie TEXT")
        if "cantidad_bocas" not in disp_cols:
            conn.execute("ALTER TABLE dispositivos_red ADD COLUMN cantidad_bocas INTEGER")
        if "bocas_fibra" not in disp_cols:
            conn.execute("ALTER TABLE dispositivos_red ADD COLUMN bocas_fibra INTEGER")
        if "plantilla" not in disp_cols:
            conn.execute("ALTER TABLE dispositivos_red ADD COLUMN plantilla TEXT DEFAULT 'generico'")
        if "mac" not in disp_cols:
            conn.execute("ALTER TABLE dispositivos_red ADD COLUMN mac TEXT")
        if "ciudad" not in disp_cols:
            conn.execute("ALTER TABLE dispositivos_red ADD COLUMN ciudad TEXT")
        if "piso" not in disp_cols:
            conn.execute("ALTER TABLE dispositivos_red ADD COLUMN piso TEXT")
        if "estado" not in disp_cols:
            conn.execute("ALTER TABLE dispositivos_red ADD COLUMN estado TEXT DEFAULT 'Usado'")
        if "fecha_ingreso" not in disp_cols:
            conn.execute("ALTER TABLE dispositivos_red ADD COLUMN fecha_ingreso TEXT")
        if "enlace" not in disp_cols:
            conn.execute("ALTER TABLE dispositivos_red ADD COLUMN enlace TEXT")
        if "firebase_id" not in disp_cols:
            conn.execute("ALTER TABLE dispositivos_red ADD COLUMN firebase_id TEXT")
        if "actualizado_en" not in disp_cols:
            conn.execute("ALTER TABLE dispositivos_red ADD COLUMN actualizado_en TEXT")
    usr_cols = {row["name"] for row in conn.execute("PRAGMA table_info(usuarios)")}
    if usr_cols:
        if "foto_perfil" not in usr_cols:
            conn.execute("ALTER TABLE usuarios ADD COLUMN foto_perfil TEXT")
        if "departamento" not in usr_cols:
            conn.execute("ALTER TABLE usuarios ADD COLUMN departamento TEXT")
        if "ciudad" not in usr_cols:
            conn.execute("ALTER TABLE usuarios ADD COLUMN ciudad TEXT")
        if "lugar_trabajo" not in usr_cols:
            conn.execute("ALTER TABLE usuarios ADD COLUMN lugar_trabajo TEXT DEFAULT 'Presencial'")
        if "sistemas_autorizados" not in usr_cols:
            conn.execute("ALTER TABLE usuarios ADD COLUMN sistemas_autorizados TEXT")
        if "tipo_vpn" not in usr_cols:
            conn.execute("ALTER TABLE usuarios ADD COLUMN tipo_vpn TEXT")
        if "vpn_activa" not in usr_cols:
            conn.execute("ALTER TABLE usuarios ADD COLUMN vpn_activa INTEGER DEFAULT 0")
        if "firebase_id" not in usr_cols:
            conn.execute("ALTER TABLE usuarios ADD COLUMN firebase_id TEXT")
        if "actualizado_en" not in usr_cols:
            conn.execute("ALTER TABLE usuarios ADD COLUMN actualizado_en TEXT")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS departamentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL UNIQUE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ciudades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL UNIQUE
        )
    """)
    ciudad_count = conn.execute("SELECT COUNT(*) AS c FROM ciudades").fetchone()["c"]
    if ciudad_count == 0:
        for nombre in ("Antofagasta", "Arica", "Iquique"):
            conn.execute("INSERT OR IGNORE INTO ciudades (nombre) VALUES (?)", (nombre,))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conexiones_dispositivos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dispositivo_id INTEGER NOT NULL,
            puerto TEXT NOT NULL,
            destino_dispositivo_id INTEGER NOT NULL,
            ts TEXT,
            UNIQUE(dispositivo_id, puerto)
        )
    """)
    conn.commit()
    conn.close()


def list_scan_files(results_dir: Path):
    if not results_dir.exists():
        return []
    return sorted(results_dir.glob("scan_*.json"), reverse=True)


def apply_scan_results(subred, results, source="monitor", offline_after_misses=2):
    """
    Aplica los resultados de un escaneo (vivos y no-vivos) a la base de datos:
    actualiza cada equipo ya conocido, inserta los nuevos que si respondieron,
    y registra un evento cuando un equipo cambia de estado (online/offline/nuevo).
    Devuelve la lista de eventos generados en esta pasada.

    Para evitar falsos positivos de "offline" por un hipo de red o de firewall
    en un solo ciclo (ping/puerto que no respondio a tiempo mientras el equipo
    seguia realmente prendido), un equipo solo se marca offline despues de
    `offline_after_misses` ciclos seguidos sin respuesta. Mientras el conteo de
    fallos no llegue al umbral, el equipo se mantiene en_linea=1 (se actualiza
    fallos_consecutivos pero no se dispara el evento/toast de offline).
    """
    now = datetime.now().isoformat()
    conn = get_connection()
    eventos = []

    for h in results:
        ip = h["ip"]
        alive = bool(h.get("alive"))
        existing = conn.execute(
            "SELECT id, en_linea, fallos_consecutivos FROM equipos WHERE ip = ?", (ip,)
        ).fetchone()

        if existing:
            eq_id = existing["id"]
            was_online = bool(existing["en_linea"])

            if alive:
                open_ports_json = json.dumps(h.get("open_ports", []))
                conn.execute(
                    """
                    UPDATE equipos
                       SET hostname = COALESCE(?, hostname),
                           mac = COALESCE(?, mac),
                           subred = ?,
                           open_ports = ?,
                           confidence_score = ?,
                           confidence_label = ?,
                           metodo_deteccion = ?,
                           ultima_deteccion = ?,
                           ultimo_scan_file = ?,
                           en_linea = 1,
                           fallos_consecutivos = 0,
                           alerta_offline_enviada = 0,
                           desde = CASE WHEN en_linea = 0 THEN ? ELSE desde END
                     WHERE id = ?
                    """,
                    (
                        h.get("hostname"), h.get("mac"), subred, open_ports_json,
                        h.get("confidence_score"), h.get("confidence_label"), h.get("metodo_deteccion"),
                        now, source, now, eq_id,
                    ),
                )
                if not was_online:
                    eventos.append({"equipo_id": eq_id, "ip": ip, "hostname": h.get("hostname"), "tipo": "online", "ts": now})
            else:
                fallos = (existing["fallos_consecutivos"] or 0) + 1
                if was_online and fallos < offline_after_misses:
                    # todavia no llega al umbral: se cuenta el fallo pero se mantiene online
                    conn.execute(
                        "UPDATE equipos SET fallos_consecutivos = ? WHERE id = ?",
                        (fallos, eq_id),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE equipos
                           SET en_linea = 0,
                               fallos_consecutivos = ?,
                               desde = CASE WHEN en_linea = 1 THEN ? ELSE desde END
                         WHERE id = ?
                        """,
                        (fallos, now, eq_id),
                    )
                    if was_online:
                        eventos.append({"equipo_id": eq_id, "ip": ip, "hostname": h.get("hostname"), "tipo": "offline", "ts": now})
        else:
            if alive:
                open_ports_json = json.dumps(h.get("open_ports", []))
                cur = conn.execute(
                    """
                    INSERT INTO equipos (
                        ip, hostname, mac, subred, open_ports,
                        confidence_score, confidence_label, metodo_deteccion, estado_deteccion,
                        en_linea, desde, primera_deteccion, ultima_deteccion, ultimo_scan_file
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pendiente', 1, ?, ?, ?, ?)
                    """,
                    (
                        ip, h.get("hostname"), h.get("mac"), subred, open_ports_json,
                        h.get("confidence_score"), h.get("confidence_label"), h.get("metodo_deteccion"),
                        now, now, now, source,
                    ),
                )
                eventos.append({"equipo_id": cur.lastrowid, "ip": ip, "hostname": h.get("hostname"), "tipo": "nuevo", "ts": now})
            # si nunca lo hemos visto y sigue sin responder, no se guarda nada

    for ev in eventos:
        conn.execute(
            "INSERT INTO eventos (equipo_id, ip, hostname, tipo, ts) VALUES (?, ?, ?, ?, ?)",
            (ev["equipo_id"], ev["ip"], ev["hostname"], ev["tipo"], ev["ts"]),
        )

    conn.commit()
    conn.close()
    return eventos


def import_scan(scan_path: Path):
    with open(scan_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    eventos = []
    for subred, hosts in data.get("results", {}).items():
        eventos.extend(apply_scan_results(subred, hosts, source=scan_path.name))
    return eventos



def migrate_legacy_confirmations(confirm_path: Path):
    """Migra el confirmations.json del v1 (viejo esquema por IP) a la tabla equipos."""
    if not confirm_path.exists():
        return 0
    with open(confirm_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    conn = get_connection()
    count = 0
    for ip, info in data.items():
        cur = conn.execute(
            "UPDATE equipos SET estado_deteccion = ? WHERE ip = ? AND estado_deteccion = 'pendiente'",
            (info.get("status"), ip),
        )
        count += cur.rowcount
    conn.commit()
    conn.close()
    return count


def list_equipos(estado=None):
    conn = get_connection()
    if estado:
        rows = conn.execute(
            "SELECT * FROM equipos WHERE estado_deteccion = ? "
            "ORDER BY en_linea DESC, confidence_score DESC",
            (estado,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM equipos "
            "ORDER BY (estado_deteccion = 'pendiente') DESC, en_linea DESC, confidence_score DESC"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_equipo(equipo_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM equipos WHERE id = ?", (equipo_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_estado(equipo_id, estado):
    conn = get_connection()
    conn.execute("UPDATE equipos SET estado_deteccion = ? WHERE id = ?", (estado, equipo_id))
    conn.commit()
    conn.close()


def update_ficha(equipo_id, fields: dict):
    cols = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [equipo_id]
    conn = get_connection()
    conn.execute(f"UPDATE equipos SET {cols} WHERE id = ?", values)
    conn.commit()
    conn.close()


def get_equipo_by_ip(ip):
    conn = get_connection()
    row = conn.execute("SELECT id FROM equipos WHERE ip = ?", (ip,)).fetchone()
    conn.close()
    return row["id"] if row else None


def create_equipo_manual(ip, hostname=None, mac=None, marca=None, modelo=None, numero_serie=None,
                          responsable_id=None, sucursal=None, ciudad=None, departamento=None, notas=None):
    """Agrega un equipo directo al inventario sin pasar por el escaneo de red
    (por ejemplo, notebooks remotos/VPN que no siempre estan conectados al
    rango de subredes que escanea el monitor). Queda marcado con
    origen='manual' para distinguirlo en el listado."""
    now = datetime.now().isoformat()
    responsable, correo_responsable = None, None
    if responsable_id:
        usuario = get_usuario(responsable_id)
        if usuario:
            responsable = usuario["nombre"]
            correo_responsable = usuario["correo"]
    conn = get_connection()
    cur = conn.execute(
        """
        INSERT INTO equipos (
            ip, hostname, mac, subred, estado_deteccion, en_linea, desde,
            primera_deteccion, ultima_deteccion, ultimo_scan_file, origen,
            marca, modelo, numero_serie, responsable_id, responsable, correo_responsable,
            sucursal, ciudad, departamento, notas, estado_ciclo_vida
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ip, hostname, mac, "Manual (fuera de red)", "confirmado", 1, now,
            now, now, "manual", "manual",
            marca, modelo, numero_serie, responsable_id, responsable, correo_responsable,
            sucursal, ciudad, departamento, notas, "activo",
        ),
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


def find_or_create_usuario_por_nombre(nombre, cargo=None, sucursal=None):
    """Busca un responsable en el directorio por nombre (sin importar
    mayusculas/espacios); si no existe lo crea. Si existe pero le falta
    cargo o sucursal y el import trae ese dato, lo completa sin tocar el
    resto de su ficha. Usado por la importacion masiva de inventario."""
    nombre = (nombre or "").strip()
    if not nombre:
        return None
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM usuarios WHERE LOWER(TRIM(nombre)) = LOWER(?)", (nombre,)
    ).fetchone()
    if row:
        usuario = dict(row)
        updates = {}
        if cargo and not usuario.get("cargo"):
            updates["cargo"] = cargo
        if sucursal and not usuario.get("sucursal"):
            updates["sucursal"] = sucursal
        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(f"UPDATE usuarios SET {set_clause} WHERE id = ?", list(updates.values()) + [usuario["id"]])
            conn.commit()
            usuario.update(updates)
        conn.close()
        return usuario
    conn.close()
    nuevo_id = create_usuario(nombre, cargo=cargo, sucursal=sucursal)
    return get_usuario(nuevo_id)


def importar_empleados_masivo(filas):
    """Importacion masiva del Directorio de Responsables (ej. un Excel de RRHH
    con correo/cargo/departamento/VPN ya escritos a mano). Matchea por nombre
    (sin importar mayusculas/espacios): si el empleado ya existe, solo
    completa los campos que esten vacios -- nunca pisa un dato ya cargado a
    mano (a diferencia de la infraestructura, aca no hay evidencia de datos
    de prueba que haya que corregir). Si no existe, lo crea.
    Cada fila puede traer: nombre, correo, departamento, ciudad, telefono,
    lugar_trabajo, vpn_activa (True/False), tipo_vpn, cargo, sistemas_autorizados.
    Devuelve {creados, actualizados, sin_cambios, omitidos, total}.
    """
    creados = actualizados = sin_cambios = omitidos = 0
    conn = get_connection()

    campos_texto = [
        "correo", "departamento", "ciudad", "telefono", "lugar_trabajo",
        "tipo_vpn", "cargo", "sistemas_autorizados",
    ]

    for fila in filas:
        nombre = (fila.get("nombre") or "").strip()
        if not nombre:
            omitidos += 1
            continue

        existente = conn.execute(
            "SELECT * FROM usuarios WHERE LOWER(TRIM(nombre)) = LOWER(?)", (nombre,)
        ).fetchone()

        if existente:
            existente = dict(existente)
            updates = {}
            for campo in campos_texto:
                valor = fila.get(campo)
                if valor and not existente.get(campo):
                    updates[campo] = valor
            if fila.get("vpn_activa") and not existente.get("vpn_activa"):
                updates["vpn_activa"] = 1
            if updates:
                set_clause = ", ".join(f"{k} = ?" for k in updates)
                conn.execute(f"UPDATE usuarios SET {set_clause} WHERE id = ?", list(updates.values()) + [existente["id"]])
                actualizados += 1
            else:
                sin_cambios += 1
        else:
            now = datetime.now().isoformat()
            conn.execute(
                """
                INSERT INTO usuarios (
                    nombre, correo, cargo, telefono, activo, creado_en,
                    departamento, ciudad, lugar_trabajo, sistemas_autorizados, tipo_vpn, vpn_activa
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    nombre, fila.get("correo"), fila.get("cargo"), fila.get("telefono"), 1, now,
                    fila.get("departamento"), fila.get("ciudad"), fila.get("lugar_trabajo") or "Presencial",
                    fila.get("sistemas_autorizados"), fila.get("tipo_vpn"), 1 if fila.get("vpn_activa") else 0,
                ),
            )
            creados += 1

    conn.commit()
    conn.close()
    return {
        "creados": creados, "actualizados": actualizados,
        "sin_cambios": sin_cambios, "omitidos": omitidos, "total": len(filas),
    }


def importar_inventario_masivo(filas):
    """Importacion masiva de un inventario externo (ej. un Excel con datos
    ya escritos a mano de otro sistema). Por cada fila con una IP:
      - si la IP ya existe en el inventario (la detecto el scanner), solo
        completa los campos de la ficha que todavia estan vacios -- nunca
        pisa un dato que ya se cargo a mano o por escaneo.
      - si la IP no existe, crea un equipo nuevo con origen='importado'.
    Tambien crea/completa el responsable en el Directorio de Responsables
    (con su cargo) cuando la fila trae ese dato.
    Cada fila puede traer: ip, hostname, mac, os, office, antivirus,
    responsable, cargo, ciudad, sucursal, departamento, marca, modelo,
    numero_serie, en_linea (True/False/None), has_rdp (True/False).
    Devuelve un resumen {creados, actualizados, sin_cambios, omitidos, total}.
    """
    now = datetime.now().isoformat()
    creados = actualizados = sin_cambios = omitidos = 0

    campos_ficha = [
        "hostname", "mac", "marca", "modelo", "numero_serie",
        "ciudad", "sucursal", "departamento", "os", "office", "antivirus",
    ]

    for fila in filas:
        ip = (fila.get("ip") or "").strip()
        if not ip:
            omitidos += 1
            continue

        responsable_id = responsable_nombre = correo_responsable = None
        if fila.get("responsable"):
            usuario = find_or_create_usuario_por_nombre(
                fila["responsable"], cargo=fila.get("cargo"), sucursal=fila.get("sucursal")
            )
            if usuario:
                responsable_id = usuario["id"]
                responsable_nombre = usuario["nombre"]
                correo_responsable = usuario.get("correo")

        existing_id = get_equipo_by_ip(ip)

        if existing_id:
            equipo_actual = get_equipo(existing_id)
            updates = {}
            for campo in campos_ficha:
                valor = (fila.get(campo) or "").strip() if isinstance(fila.get(campo), str) else fila.get(campo)
                if valor and not equipo_actual.get(campo):
                    updates[campo] = valor
            if responsable_id and not equipo_actual.get("responsable_id"):
                updates["responsable_id"] = responsable_id
                updates["responsable"] = responsable_nombre
                updates["correo_responsable"] = correo_responsable
            if updates:
                update_ficha(existing_id, updates)
                actualizados += 1
            else:
                sin_cambios += 1
        else:
            partes_ip = ip.split(".")
            subred = ".".join(partes_ip[:3]) + ".0/24" if len(partes_ip) == 4 else None
            en_linea = 0 if fila.get("en_linea") is False else 1
            open_ports = json.dumps([{"port": 3389, "service": "rdp"}]) if fila.get("has_rdp") else "[]"
            conn = get_connection()
            conn.execute(
                """
                INSERT INTO equipos (
                    ip, hostname, mac, subred, open_ports, estado_deteccion, en_linea, desde,
                    primera_deteccion, ultima_deteccion, ultimo_scan_file, origen,
                    marca, modelo, numero_serie, responsable_id, responsable, correo_responsable,
                    sucursal, ciudad, departamento, os, office, antivirus, estado_ciclo_vida
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ip, fila.get("hostname"), fila.get("mac"), subred, open_ports,
                    "confirmado", en_linea, now, now, now, "importado", "importado",
                    fila.get("marca"), fila.get("modelo"), fila.get("numero_serie"),
                    responsable_id, responsable_nombre, correo_responsable,
                    fila.get("sucursal"), fila.get("ciudad"), fila.get("departamento"),
                    fila.get("os"), fila.get("office"), fila.get("antivirus"), "activo",
                ),
            )
            conn.commit()
            conn.close()
            creados += 1

    return {
        "creados": creados, "actualizados": actualizados,
        "sin_cambios": sin_cambios, "omitidos": omitidos, "total": len(filas),
    }


# Mapeo del "Estado" de equipo del sistema externo -> estado_ciclo_vida
# propio. Solo se usa para equipos NUEVOS (ver importar_gestion_equipos):
# para un equipo ya existente en NetWatch nunca se pisa su ciclo de vida,
# porque estado_ciclo_vida siempre trae un valor por defecto ('activo') asi
# que la regla de "solo completar si esta vacio" ya lo protege sola.
_GESTION_ESTADO_CICLO_MAP = {
    "en uso": "activo", "disponible": "bodega", "mantenimiento": "en_reparacion",
}


def _importar_gestion_usuarios(filas):
    """Mitad 'Usuarios' de importar_gestion_masiva. Matchea por nombre (sin
    importar mayusculas/espacios), igual que importar_empleados_masivo:
    nunca pisa un dato que el usuario ya tenga cargado en NetWatch, solo
    completa lo que esta vacio. 'activo'/'lugar_trabajo' (derivados del
    Estado del sistema de origen) solo se usan para completar un usuario
    NUEVO -- si el usuario ya existe en NetWatch, esos dos campos no se
    tocan, para no pisar un estado que ya se haya ajustado a mano aca."""
    creados = actualizados = sin_cambios = omitidos = 0
    conn = get_connection()
    campos_texto = ["correo", "departamento", "ciudad", "tipo_vpn"]

    # diccionario nombre-sin-acentos -> fila de usuarios, armado una sola vez.
    # Antes se matcheaba con LOWER(TRIM(nombre)) = LOWER(?) en SQL, que no
    # ignora tildes, asi que un archivo externo sin tildes ("Carlos
    # Rodriguez") creaba un usuario duplicado en vez de completar el que ya
    # existia con tilde ("Carlos Rodríguez") -- confirmado en vivo.
    usuarios_por_clave = {
        _clave_nombre(r["nombre"]): dict(r) for r in conn.execute("SELECT * FROM usuarios").fetchall()
    }

    for fila in filas:
        nombre = (fila.get("nombre") or "").strip()
        if not nombre:
            omitidos += 1
            continue

        if fila.get("departamento"):
            conn.execute("INSERT OR IGNORE INTO departamentos (nombre) VALUES (?)", (fila["departamento"],))
        if fila.get("ciudad"):
            conn.execute("INSERT OR IGNORE INTO ciudades (nombre) VALUES (?)", (fila["ciudad"],))

        existente = usuarios_por_clave.get(_clave_nombre(nombre))

        if existente:
            updates = {}
            for campo in campos_texto:
                valor = fila.get(campo)
                if valor and not existente.get(campo):
                    updates[campo] = valor
            if updates:
                set_clause = ", ".join(f"{k} = ?" for k in updates)
                conn.execute(f"UPDATE usuarios SET {set_clause} WHERE id = ?", list(updates.values()) + [existente["id"]])
                existente.update(updates)
                actualizados += 1
            else:
                sin_cambios += 1
        else:
            now = datetime.now().isoformat()
            cur = conn.execute(
                """
                INSERT INTO usuarios (
                    nombre, correo, activo, creado_en, departamento, ciudad,
                    lugar_trabajo, tipo_vpn, vpn_activa
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    nombre, fila.get("correo"), 0 if fila.get("activo") is False else 1, now,
                    fila.get("departamento"), fila.get("ciudad"),
                    fila.get("lugar_trabajo") or "Presencial", fila.get("tipo_vpn"),
                    1 if fila.get("tipo_vpn") else 0,
                ),
            )
            usuarios_por_clave[_clave_nombre(nombre)] = {"id": cur.lastrowid, "nombre": nombre, "correo": fila.get("correo")}
            creados += 1

    conn.commit()
    conn.close()
    return {
        "creados": creados, "actualizados": actualizados,
        "sin_cambios": sin_cambios, "omitidos": omitidos, "total": len(filas),
    }


def _importar_gestion_equipos(filas):
    """Mitad 'Equipos' de importar_gestion_masiva. Matchea primero por IP
    real (si la fila trae una y ya existe en NetWatch, es el mismo equipo
    fisico que ya detecto el scanner); si no, por hostname. Si un equipo no
    tiene IP real (columna 'Dinamica'), se usa el propio hostname como
    identificador unico -- mismo patron que 'IP o identificador unico' del
    alta manual, porque equipos.ip es NOT NULL UNIQUE.
    Si un equipo tiene mas de un responsable asignado (terminales
    compartidos), el primero queda como responsable_id y el resto se deja
    anotado en 'notas' para no perder el dato."""
    now = datetime.now().isoformat()
    creados = actualizados = sin_cambios = omitidos = 0
    campos_ficha = [
        "marca", "modelo", "numero_serie", "ciudad", "sucursal",
        "cpu", "ram", "almacenamiento", "gpu", "os", "office", "antivirus",
    ]
    conn = get_connection()
    usuarios_por_clave = {
        _clave_nombre(r["nombre"]): dict(r) for r in conn.execute("SELECT * FROM usuarios").fetchall()
    }

    for fila in filas:
        hostname = (fila.get("hostname") or "").strip()
        if not hostname:
            omitidos += 1
            continue

        ip_real = fila.get("ip")

        equipo_id = None
        if ip_real:
            row = conn.execute("SELECT id FROM equipos WHERE ip = ?", (ip_real,)).fetchone()
            if row:
                equipo_id = row["id"]
        if not equipo_id:
            row = conn.execute(
                "SELECT id FROM equipos WHERE LOWER(TRIM(hostname)) = LOWER(?)", (hostname,)
            ).fetchone()
            if row:
                equipo_id = row["id"]

        responsable_id = responsable_nombre = correo_responsable = None
        nombres_resp = fila.get("responsables") or []
        if nombres_resp:
            r = usuarios_por_clave.get(_clave_nombre(nombres_resp[0]))
            if r:
                responsable_id, responsable_nombre, correo_responsable = r["id"], r["nombre"], r["correo"]

        partes_notas = []
        if fila.get("descripcion"):
            partes_notas.append(fila["descripcion"])
        if len(nombres_resp) > 1:
            partes_notas.append("Equipo compartido tambien con: " + ", ".join(nombres_resp[1:]))
        notas_nuevas = " · ".join(partes_notas) if partes_notas else None

        estado_ciclo = _GESTION_ESTADO_CICLO_MAP.get((fila.get("estado") or "").strip().lower())

        if equipo_id:
            equipo_actual = dict(conn.execute("SELECT * FROM equipos WHERE id = ?", (equipo_id,)).fetchone())
            updates = {}
            for campo in campos_ficha:
                valor = fila.get(campo)
                if valor and not equipo_actual.get(campo):
                    updates[campo] = valor
            if notas_nuevas and not equipo_actual.get("notas"):
                updates["notas"] = notas_nuevas
            if responsable_id and not equipo_actual.get("responsable_id"):
                updates["responsable_id"] = responsable_id
                updates["responsable"] = responsable_nombre
                updates["correo_responsable"] = correo_responsable
            if estado_ciclo and not equipo_actual.get("estado_ciclo_vida"):
                updates["estado_ciclo_vida"] = estado_ciclo
            if updates:
                # mismo patron que update_ficha(), pero reusando esta misma
                # conexion -- abrir una conexion nueva mientras esta sigue
                # abierta con cambios sin commitear deja la base "locked".
                set_clause = ", ".join(f"{k} = ?" for k in updates)
                conn.execute(f"UPDATE equipos SET {set_clause} WHERE id = ?", list(updates.values()) + [equipo_id])
                actualizados += 1
            else:
                sin_cambios += 1
        else:
            identificador = ip_real or hostname
            sufijo = 1
            base = identificador
            while conn.execute("SELECT 1 FROM equipos WHERE ip = ?", (identificador,)).fetchone():
                sufijo += 1
                identificador = f"{base}-{sufijo}"

            subred = None
            if ip_real:
                partes = ip_real.split(".")
                if len(partes) == 4:
                    subred = ".".join(partes[:3]) + ".0/24"

            conn.execute(
                """
                INSERT INTO equipos (
                    ip, hostname, subred, estado_deteccion, en_linea, desde,
                    primera_deteccion, ultima_deteccion, ultimo_scan_file, origen,
                    marca, modelo, numero_serie, responsable_id, responsable, correo_responsable,
                    sucursal, ciudad, cpu, ram, almacenamiento, gpu, os, office, antivirus,
                    notas, estado_ciclo_vida
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    identificador, hostname, subred, "confirmado", 1, now, now, now,
                    "importado", "importado",
                    fila.get("marca"), fila.get("modelo"), fila.get("numero_serie"),
                    responsable_id, responsable_nombre, correo_responsable,
                    fila.get("sucursal"), fila.get("ciudad"),
                    fila.get("cpu"), fila.get("ram"), fila.get("almacenamiento"), fila.get("gpu"),
                    fila.get("os"), fila.get("office"), fila.get("antivirus"),
                    notas_nuevas, estado_ciclo or "activo",
                ),
            )
            creados += 1

    conn.commit()
    conn.close()
    return {
        "creados": creados, "actualizados": actualizados,
        "sin_cambios": sin_cambios, "omitidos": omitidos, "total": len(filas),
    }


def importar_gestion_masiva(usuarios_filas, equipos_filas):
    """Importacion masiva desde un sistema externo de gestion de usuarios y
    equipos (export .xlsx con hojas Usuarios/Equipos/Departamentos, distinto
    formato al resto de los importadores de este archivo). Devuelve
    {usuarios: {...}, equipos: {...}} con el mismo resumen
    creados/actualizados/sin_cambios/omitidos/total de cada mitad."""
    resumen_usuarios = _importar_gestion_usuarios(usuarios_filas)
    resumen_equipos = _importar_gestion_equipos(equipos_filas)
    return {"usuarios": resumen_usuarios, "equipos": resumen_equipos}


def list_recent_events(limit=20):
    """Trae los ultimos cambios (online/offline/nuevo) con el responsable
    actual del equipo (si tiene uno asignado), para que el panel lateral y
    los toasts puedan mostrar el nombre de la persona en vez de solo el
    hostname, y para que sean clickeables hacia su ficha."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT eventos.*, equipos.responsable AS responsable
        FROM eventos
        LEFT JOIN equipos ON equipos.id = eventos.equipo_id
        ORDER BY eventos.id DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# Disponibilidad / historial (a partir de la tabla eventos)
# --------------------------------------------------------------------------

def _calcular_pct_online(eventos_ts_tipo, inicio_dt, fin_dt, online_al_inicio=True):
    """Dada una lista de (datetime, tipo) ordenada ascendente (solo tipo
    'online'/'offline'), calcula el % de tiempo online entre inicio y fin,
    asumiendo el estado inicial indicado hasta el primer evento real. Se usa
    tanto para la ficha de un equipo como para el ranking general."""
    total = (fin_dt - inicio_dt).total_seconds()
    if total <= 0:
        return 100.0, 0

    offline_segundos = 0.0
    caidas = 0
    estado_online = online_al_inicio
    cursor = inicio_dt

    for ts, tipo in eventos_ts_tipo:
        if ts < inicio_dt:
            continue
        if ts > fin_dt:
            break
        if estado_online and tipo == "offline":
            cursor = ts
            estado_online = False
            caidas += 1
        elif not estado_online and tipo == "online":
            offline_segundos += (ts - cursor).total_seconds()
            cursor = ts
            estado_online = True
        # eventos repetidos del mismo tipo seguido (duplicados) se ignoran

    if not estado_online:
        offline_segundos += (fin_dt - cursor).total_seconds()

    pct_online = max(0.0, min(100.0, 100.0 * (1 - offline_segundos / total)))
    return round(pct_online, 1), caidas


def _disponibilidad_desde_conn(conn, equipo_id, primera_deteccion, dias):
    fin_dt = datetime.now()
    inicio_dt = fin_dt - timedelta(days=dias)
    if primera_deteccion:
        try:
            primera = datetime.fromisoformat(primera_deteccion)
            if primera > inicio_dt:
                inicio_dt = primera
        except ValueError:
            pass

    filas = conn.execute(
        "SELECT ts, tipo FROM eventos WHERE equipo_id = ? AND tipo IN ('online','offline') ORDER BY ts ASC",
        (equipo_id,),
    ).fetchall()

    eventos_parseados = []
    for f in filas:
        try:
            eventos_parseados.append((datetime.fromisoformat(f["ts"]), f["tipo"]))
        except ValueError:
            continue

    # Si el primer evento visible en la ventana es "offline", lo mas probable
    # es que antes estuviera online (asi arranca todo equipo nuevo).
    online_al_inicio = True
    if eventos_parseados and eventos_parseados[0][1] == "offline" and eventos_parseados[0][0] <= inicio_dt:
        online_al_inicio = False

    pct_online, caidas = _calcular_pct_online(eventos_parseados, inicio_dt, fin_dt, online_al_inicio)
    return {"pct_online": pct_online, "caidas": caidas, "dias": dias}


def calcular_disponibilidad(equipo_id, dias=30):
    """% de tiempo online de un equipo en los ultimos `dias` dias, calculado
    reconstruyendo las transiciones online/offline desde la tabla eventos.
    Devuelve None si el equipo no existe."""
    conn = get_connection()
    equipo = conn.execute("SELECT primera_deteccion FROM equipos WHERE id = ?", (equipo_id,)).fetchone()
    if not equipo:
        conn.close()
        return None
    resultado = _disponibilidad_desde_conn(conn, equipo_id, equipo["primera_deteccion"], dias)
    conn.close()
    return resultado


def ranking_disponibilidad(dias=30, limite=15):
    """Los equipos con peor disponibilidad en los ultimos `dias` dias
    (ordenados por % online ascendente, luego por mas caidas primero) --
    para encontrar el que anda fallando seguido, no solo el que esta caido
    ahora mismo. Deja afuera los equipos manuales (sin deteccion real) y los
    que no tuvieron ninguna caida en la ventana."""
    conn = get_connection()
    equipos = conn.execute(
        "SELECT id, hostname, ip, responsable, sucursal, ciudad, en_linea, primera_deteccion "
        "FROM equipos WHERE origen != 'manual'"
    ).fetchall()

    resultados = []
    for e in equipos:
        disp = _disponibilidad_desde_conn(conn, e["id"], e["primera_deteccion"], dias)
        if disp["caidas"] == 0:
            continue
        resultados.append({
            "id": e["id"], "hostname": e["hostname"], "ip": e["ip"],
            "responsable": e["responsable"], "sucursal": e["sucursal"], "ciudad": e["ciudad"],
            "en_linea": bool(e["en_linea"]),
            "pct_online": disp["pct_online"], "caidas": disp["caidas"],
        })
    conn.close()

    resultados.sort(key=lambda r: (r["pct_online"], -r["caidas"]))
    return resultados[:limite]


def equipos_criticos_pendientes_alerta(umbral_minutos=15):
    """Equipos marcados como 'critico', offline hace mas del umbral elegido,
    y a los que todavia no se les mando el aviso para esta caida (para no
    mandar el mismo aviso de nuevo en cada ciclo mientras siga caido)."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT id, ip, hostname, responsable, sucursal, ciudad, desde
        FROM equipos
        WHERE critico = 1 AND en_linea = 0 AND alerta_offline_enviada = 0
          AND desde IS NOT NULL
        """
    ).fetchall()
    conn.close()

    pendientes = []
    ahora = datetime.now()
    for r in rows:
        try:
            desde_dt = datetime.fromisoformat(r["desde"])
        except (ValueError, TypeError):
            continue
        minutos_offline = (ahora - desde_dt).total_seconds() / 60
        if minutos_offline >= umbral_minutos:
            fila = dict(r)
            fila["minutos_offline"] = round(minutos_offline)
            pendientes.append(fila)
    return pendientes


def marcar_alerta_offline_enviada(equipo_id):
    conn = get_connection()
    conn.execute("UPDATE equipos SET alerta_offline_enviada = 1 WHERE id = ?", (equipo_id,))
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------
# Tickets de soporte
# --------------------------------------------------------------------------

def create_ticket(equipo_id, titulo, descripcion=None, prioridad="normal", asignado_a=None):
    now = datetime.now().isoformat()
    conn = get_connection()
    cur = conn.execute(
        """
        INSERT INTO tickets (equipo_id, titulo, descripcion, prioridad, estado, asignado_a, creado_en, actualizado_en)
        VALUES (?, ?, ?, ?, 'abierto', ?, ?, ?)
        """,
        (equipo_id, titulo, descripcion, prioridad, asignado_a, now, now),
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


def list_tickets_for_equipo(equipo_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM tickets WHERE equipo_id = ? "
        "ORDER BY (estado = 'resuelto') ASC, (prioridad = 'alta') DESC, id DESC",
        (equipo_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_all_tickets(estado=None, prioridad=None):
    conn = get_connection()
    query = (
        "SELECT tickets.*, equipos.ip AS equipo_ip, equipos.hostname AS equipo_hostname "
        "FROM tickets JOIN equipos ON tickets.equipo_id = equipos.id WHERE 1=1"
    )
    params = []
    if estado:
        query += " AND tickets.estado = ?"
        params.append(estado)
    if prioridad:
        query += " AND tickets.prioridad = ?"
        params.append(prioridad)
    query += " ORDER BY (tickets.estado = 'resuelto') ASC, (tickets.prioridad = 'alta') DESC, tickets.id DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_ticket(ticket_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_ticket_estado(ticket_id, estado):
    now = datetime.now().isoformat()
    conn = get_connection()
    if estado == "resuelto":
        conn.execute(
            "UPDATE tickets SET estado = ?, actualizado_en = ?, resuelto_en = ? WHERE id = ?",
            (estado, now, now, ticket_id),
        )
    else:
        conn.execute(
            "UPDATE tickets SET estado = ?, actualizado_en = ?, resuelto_en = NULL WHERE id = ?",
            (estado, now, ticket_id),
        )
    conn.commit()
    conn.close()


def count_open_tickets():
    conn = get_connection()
    row = conn.execute("SELECT COUNT(*) AS c FROM tickets WHERE estado != 'resuelto'").fetchone()
    conn.close()
    return row["c"]


def get_open_ticket_counts():
    conn = get_connection()
    rows = conn.execute(
        "SELECT equipo_id, COUNT(*) AS c FROM tickets WHERE estado != 'resuelto' GROUP BY equipo_id"
    ).fetchall()
    conn.close()
    return {r["equipo_id"]: r["c"] for r in rows}


# --------------------------------------------------------------------------
# Historial de conexiones RDP
# --------------------------------------------------------------------------

def log_rdp_connection(equipo_id, ip, hostname, origen_ip):
    now = datetime.now().isoformat()
    conn = get_connection()
    conn.execute(
        "INSERT INTO rdp_history (equipo_id, ip, hostname, origen_ip, ts) VALUES (?, ?, ?, ?, ?)",
        (equipo_id, ip, hostname, origen_ip, now),
    )
    conn.commit()
    conn.close()


def list_rdp_history_for_equipo(equipo_id, limit=10):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM rdp_history WHERE equipo_id = ? ORDER BY id DESC LIMIT ?",
        (equipo_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# Directorio de responsables (usuarios)
# --------------------------------------------------------------------------

def create_usuario(nombre, correo=None, cargo=None, sucursal=None, telefono=None,
                    foto_perfil=None, departamento=None, ciudad=None, lugar_trabajo="Presencial",
                    sistemas_autorizados=None, tipo_vpn=None, vpn_activa=0, activo=1):
    now = datetime.now().isoformat()
    conn = get_connection()
    cur = conn.execute(
        """
        INSERT INTO usuarios (
            nombre, correo, cargo, sucursal, telefono, activo, creado_en,
            foto_perfil, departamento, ciudad, lugar_trabajo, sistemas_autorizados,
            tipo_vpn, vpn_activa
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (nombre, correo, cargo, sucursal, telefono, 1 if activo else 0, now,
         foto_perfil, departamento, ciudad, lugar_trabajo, sistemas_autorizados,
         tipo_vpn, 1 if vpn_activa else 0),
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


def list_usuarios(solo_activos=False):
    conn = get_connection()
    if solo_activos:
        rows = conn.execute("SELECT * FROM usuarios WHERE activo = 1 ORDER BY nombre").fetchall()
    else:
        rows = conn.execute("SELECT * FROM usuarios ORDER BY activo DESC, nombre").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_usuario(usuario_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM usuarios WHERE id = ?", (usuario_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_usuario(usuario_id, nombre, correo=None, cargo=None, sucursal=None, telefono=None,
                    foto_perfil=None, departamento=None, ciudad=None, lugar_trabajo="Presencial",
                    sistemas_autorizados=None, tipo_vpn=None, vpn_activa=0, activo=1, actualizar_foto=True):
    conn = get_connection()
    if actualizar_foto:
        conn.execute(
            """
            UPDATE usuarios
               SET nombre = ?, correo = ?, cargo = ?, sucursal = ?, telefono = ?,
                   foto_perfil = ?, departamento = ?, ciudad = ?, lugar_trabajo = ?,
                   sistemas_autorizados = ?, tipo_vpn = ?, vpn_activa = ?, activo = ?
             WHERE id = ?
            """,
            (nombre, correo, cargo, sucursal, telefono,
             foto_perfil, departamento, ciudad, lugar_trabajo,
             sistemas_autorizados, tipo_vpn, 1 if vpn_activa else 0, 1 if activo else 0, usuario_id),
        )
    else:
        # no se subio/pego una foto nueva: conserva la que ya tenia
        conn.execute(
            """
            UPDATE usuarios
               SET nombre = ?, correo = ?, cargo = ?, sucursal = ?, telefono = ?,
                   departamento = ?, ciudad = ?, lugar_trabajo = ?,
                   sistemas_autorizados = ?, tipo_vpn = ?, vpn_activa = ?, activo = ?
             WHERE id = ?
            """,
            (nombre, correo, cargo, sucursal, telefono,
             departamento, ciudad, lugar_trabajo,
             sistemas_autorizados, tipo_vpn, 1 if vpn_activa else 0, 1 if activo else 0, usuario_id),
        )
    conn.commit()
    conn.close()


def delete_usuario(usuario_id):
    """Elimina un empleado del directorio. Los equipos que lo tenian como
    responsable quedan sin responsable_id (pero conservan el nombre/correo
    como registro historico en los campos responsable/correo_responsable)."""
    conn = get_connection()
    conn.execute("UPDATE equipos SET responsable_id = NULL WHERE responsable_id = ?", (usuario_id,))
    conn.execute("DELETE FROM usuarios WHERE id = ?", (usuario_id,))
    conn.commit()
    conn.close()


def delete_usuarios(usuario_ids):
    """Version en bloque de delete_usuario -- pensado para limpiar de una
    varios registros que en realidad son nombres de equipos/dispositivos
    (ej. 'Impresora', 'Switch Juniper') que quedaron cargados como si fueran
    empleados por una importacion vieja. Devuelve la cantidad borrada."""
    ids = [int(i) for i in (usuario_ids or [])]
    if not ids:
        return 0
    conn = get_connection()
    placeholders = ",".join("?" for _ in ids)
    conn.execute(f"UPDATE equipos SET responsable_id = NULL WHERE responsable_id IN ({placeholders})", ids)
    cur = conn.execute(f"DELETE FROM usuarios WHERE id IN ({placeholders})", ids)
    borrados = cur.rowcount
    conn.commit()
    conn.close()
    return borrados


def delete_equipos(equipo_ids):
    """Elimina uno o varios equipos del inventario de una sola vez (pensado
    para limpiar duplicados/basura que trajo una importacion masiva, ej.
    'nbsoportemc' repetido o un hostname que en realidad era un N. de serie).
    Borra tambien su historial asociado (eventos, conexiones RDP, tickets)
    para no dejar registros huerfanos apuntando a un equipo que ya no
    existe. Devuelve la cantidad de equipos borrados."""
    ids = [int(i) for i in (equipo_ids or [])]
    if not ids:
        return 0
    conn = get_connection()
    placeholders = ",".join("?" for _ in ids)
    conn.execute(f"DELETE FROM eventos WHERE equipo_id IN ({placeholders})", ids)
    conn.execute(f"DELETE FROM rdp_history WHERE equipo_id IN ({placeholders})", ids)
    conn.execute(f"DELETE FROM tickets WHERE equipo_id IN ({placeholders})", ids)
    cur = conn.execute(f"DELETE FROM equipos WHERE id IN ({placeholders})", ids)
    borrados = cur.rowcount
    conn.commit()
    conn.close()
    return borrados


def get_equipos_count_por_responsable():
    conn = get_connection()
    rows = conn.execute(
        "SELECT responsable_id, COUNT(*) AS c FROM equipos WHERE responsable_id IS NOT NULL GROUP BY responsable_id"
    ).fetchall()
    conn.close()
    return {r["responsable_id"]: r["c"] for r in rows}


def list_equipos_por_responsable(usuario_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, ip, hostname, en_linea, estado_deteccion FROM equipos "
        "WHERE responsable_id = ? ORDER BY hostname, ip",
        (usuario_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_equipos_basico():
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, ip, hostname, responsable_id FROM equipos ORDER BY hostname, ip"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_responsable_equipo(equipo_id, usuario_id):
    """Asigna (o quita, si usuario_id es None) el responsable de un equipo,
    copiando tambien nombre/correo para mostrarlos sin necesitar join."""
    responsable, correo_responsable = None, None
    if usuario_id:
        usuario = get_usuario(usuario_id)
        if usuario:
            responsable = usuario["nombre"]
            correo_responsable = usuario["correo"]
    conn = get_connection()
    conn.execute(
        "UPDATE equipos SET responsable_id = ?, responsable = ?, correo_responsable = ? WHERE id = ?",
        (usuario_id, responsable, correo_responsable, equipo_id),
    )
    conn.commit()
    conn.close()


def update_usuario_estado(usuario_id, activo):
    conn = get_connection()
    conn.execute("UPDATE usuarios SET activo = ? WHERE id = ?", (1 if activo else 0, usuario_id))
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------
# Catalogos administrables: departamentos y ciudades (para el directorio)
# --------------------------------------------------------------------------

def list_departamentos():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM departamentos ORDER BY nombre").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_departamento(nombre):
    conn = get_connection()
    cur = conn.execute("INSERT OR IGNORE INTO departamentos (nombre) VALUES (?)", (nombre,))
    conn.commit()
    conn.close()
    return cur.lastrowid


def delete_departamento(departamento_id):
    conn = get_connection()
    conn.execute("DELETE FROM departamentos WHERE id = ?", (departamento_id,))
    conn.commit()
    conn.close()


def list_ciudades():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM ciudades ORDER BY nombre").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_ciudad(nombre):
    conn = get_connection()
    cur = conn.execute("INSERT OR IGNORE INTO ciudades (nombre) VALUES (?)", (nombre,))
    conn.commit()
    conn.close()
    return cur.lastrowid


def delete_ciudad(ciudad_id):
    conn = get_connection()
    conn.execute("DELETE FROM ciudades WHERE id = ?", (ciudad_id,))
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------
# Topologia (dispositivos de red: switch/router/fortinet/otro, mapeo manual de puertos)
# --------------------------------------------------------------------------

TIPOS_DISPOSITIVO = ["switch", "router", "fortinet", "otro"]
TIPO_DISPOSITIVO_LABELS = {
    "switch": "Switch administrable",
    "router": "Router",
    "fortinet": "Firewall Fortinet",
    "otro": "Otro dispositivo",
}
ESTADOS_DISPOSITIVO = ["Nuevo", "Usado", "En reparacion", "Fuera de servicio"]

# Plantillas de puertos: layout real de modelos comunes, para que el mapa visual
# se parezca al equipo de verdad en vez de una numeracion generica.
# Cada entrada es una lista ordenada de {"label": ..., "tipo": ...}.
# tipo puede ser: cobre, fibra, wan, dmz, consola.
PLANTILLAS_PUERTOS = {
    "cisco_24_2sfp": {
        "nombre": "Switch Cisco 24 puertos + 2 SFP fibra",
        "puertos": (
            [{"label": str(i), "tipo": "cobre"} for i in range(1, 25)]
            + [{"label": "SFP1", "tipo": "fibra"}, {"label": "SFP2", "tipo": "fibra"}]
        ),
    },
    "cisco_48_2sfp": {
        "nombre": "Switch Cisco 48 puertos + 2 SFP fibra",
        "puertos": (
            [{"label": str(i), "tipo": "cobre"} for i in range(1, 49)]
            + [{"label": "SFP1", "tipo": "fibra"}, {"label": "SFP2", "tipo": "fibra"}]
        ),
    },
    "fortinet_fg60f": {
        "nombre": "Firewall Fortinet FG-60F",
        "puertos": [
            {"label": "CNS", "tipo": "consola"},
            {"label": "WAN2", "tipo": "wan"},
            {"label": "WAN1", "tipo": "wan"},
            {"label": "DMZ", "tipo": "dmz"},
            {"label": "B", "tipo": "dmz"},
            {"label": "A", "tipo": "dmz"},
            {"label": "5", "tipo": "cobre"},
            {"label": "4", "tipo": "cobre"},
            {"label": "3", "tipo": "cobre"},
            {"label": "2", "tipo": "cobre"},
            {"label": "1", "tipo": "cobre"},
        ],
    },
    "conversor_medios": {
        "nombre": "Conversor de medios fibra/cobre + consola (ej. Raisecom RC552-FE)",
        "puertos": [
            {"label": "OPT", "tipo": "fibra"},
            {"label": "FE", "tipo": "cobre"},
            {"label": "CNS", "tipo": "consola"},
        ],
    },
    "ont_router_gpon": {
        "nombre": "Router/ONT GPON, 1 WAN fibra + 4 LAN cobre (ej. Mitrastar GPT-2741GNAC)",
        "puertos": [
            {"label": "WAN-GPON", "tipo": "fibra"},
            {"label": "LAN1", "tipo": "cobre"},
            {"label": "LAN2", "tipo": "cobre"},
            {"label": "LAN3", "tipo": "cobre"},
            {"label": "LAN4", "tipo": "cobre"},
        ],
    },
    "cisco_2901_isr": {
        "nombre": "Router Cisco 2901 ISR G2 - puertos fijos (EHWIC no incluidos, varian por equipo)",
        "puertos": [
            {"label": "GE0/0", "tipo": "cobre"},
            {"label": "GE0/1", "tipo": "cobre"},
            {"label": "CON", "tipo": "consola"},
        ],
    },
    "juniper_ex2200_24p": {
        "nombre": "Switch Juniper EX2200-24P-4G (24 PoE cobre + 4 SFP fibra)",
        "puertos": (
            [{"label": str(i), "tipo": "cobre"} for i in range(1, 25)]
            + [{"label": f"SFP{i}", "tipo": "fibra"} for i in range(1, 5)]
        ),
    },
    "juniper_ex2200_48p": {
        "nombre": "Switch Juniper EX2200-48P-4G (48 PoE cobre + 4 SFP fibra)",
        "puertos": (
            [{"label": str(i), "tipo": "cobre"} for i in range(1, 49)]
            + [{"label": f"SFP{i}", "tipo": "fibra"} for i in range(1, 5)]
        ),
    },
}


def get_puertos_definicion(d):
    """Devuelve la lista ordenada de bocas [{label, tipo}] de un dispositivo:
    si tiene una plantilla real conocida, usa su layout exacto; si no
    (plantilla "generico" o vacia), arma la grilla numerada a partir de
    cantidad_bocas / bocas_fibra (compatibilidad con dispositivos ya creados)."""
    plantilla = d.get("plantilla") or "generico"
    if plantilla in PLANTILLAS_PUERTOS:
        return list(PLANTILLAS_PUERTOS[plantilla]["puertos"])
    puertos = []
    for i in range(1, (d.get("cantidad_bocas") or 0) + 1):
        puertos.append({"label": str(i), "tipo": "cobre"})
    for i in range(1, (d.get("bocas_fibra") or 0) + 1):
        puertos.append({"label": f"F{i}", "tipo": "fibra"})
    return puertos


def create_dispositivo(nombre, tipo="switch", marca=None, modelo=None, numero_serie=None,
                        cantidad_bocas=None, bocas_fibra=None, plantilla="generico",
                        ip=None, mac=None, sucursal=None, ciudad=None, ubicacion=None, piso=None,
                        estado="Usado", fecha_ingreso=None, notas=None, enlace=None):
    now = datetime.now().isoformat()
    conn = get_connection()
    cur = conn.execute(
        """
        INSERT INTO dispositivos_red (
            nombre, tipo, marca, modelo, numero_serie, cantidad_bocas, bocas_fibra, plantilla,
            ip, mac, sucursal, ciudad, ubicacion, piso, estado, fecha_ingreso, notas, enlace, creado_en
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (nombre, tipo, marca, modelo, numero_serie, cantidad_bocas, bocas_fibra, plantilla,
         ip, mac, sucursal, ciudad, ubicacion, piso, estado, fecha_ingreso, notas, enlace, now),
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


def list_dispositivos():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM dispositivos_red ORDER BY sucursal, tipo, nombre").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_dispositivo(dispositivo_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM dispositivos_red WHERE id = ?", (dispositivo_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_dispositivo(dispositivo_id, nombre, tipo="switch", marca=None, modelo=None, numero_serie=None,
                       cantidad_bocas=None, bocas_fibra=None, plantilla="generico",
                       ip=None, mac=None, sucursal=None, ciudad=None, ubicacion=None, piso=None,
                       estado="Usado", fecha_ingreso=None, notas=None, enlace=None):
    conn = get_connection()
    conn.execute(
        """
        UPDATE dispositivos_red
           SET nombre = ?, tipo = ?, marca = ?, modelo = ?, numero_serie = ?, cantidad_bocas = ?,
               bocas_fibra = ?, plantilla = ?, ip = ?, mac = ?, sucursal = ?, ciudad = ?,
               ubicacion = ?, piso = ?, estado = ?, fecha_ingreso = ?, notas = ?, enlace = ?
         WHERE id = ?
        """,
        (nombre, tipo, marca, modelo, numero_serie, cantidad_bocas, bocas_fibra, plantilla,
         ip, mac, sucursal, ciudad, ubicacion, piso, estado, fecha_ingreso, notas, enlace, dispositivo_id),
    )
    conn.commit()
    conn.close()


def _inferir_tipo_y_plantilla(marca, modelo, bocas_num):
    """Adivina el tipo de elemento y la plantilla de puertos a partir de
    marca/modelo, para que el mapa visual de bocas quede listo de una sin
    tener que configurar cada dispositivo importado a mano."""
    marca_l = (marca or "").lower()
    modelo_l = (modelo or "").lower()

    if "fortinet" in marca_l:
        return "fortinet", "fortinet_fg60f"
    if "cisco" in marca_l and any(m in modelo_l for m in ("2901", "2900", "2800", "2811", "2911", "isr")):
        return "router", "cisco_2901_isr"
    if "raisecom" in marca_l or "conversor" in modelo_l:
        return "otro", "conversor_medios"
    if ("movistar" in marca_l or "huawei" in marca_l or "gpt" in modelo_l
            or "ont" in modelo_l or "modem" in modelo_l or "optixstar" in marca_l):
        return "otro", "ont_router_gpon"
    if "juniper" in marca_l and "ex2200" in modelo_l:
        return ("switch", "juniper_ex2200_48p") if bocas_num and bocas_num >= 48 else ("switch", "juniper_ex2200_24p")
    if "cisco" in marca_l or "tp-link" in marca_l:
        return ("switch", "cisco_48_2sfp") if bocas_num and bocas_num >= 48 else ("switch", "cisco_24_2sfp")
    return "otro", "generico"


def _parsear_bocas(bocas_raw):
    match = re.search(r"\d+", bocas_raw or "")
    return int(match.group()) if match else None


_ESTADO_DISPOSITIVO_MAP = {
    "usado": "Usado", "nuevo": "Nuevo", "apagado": "Fuera de servicio", "malo": "En reparacion",
}


def importar_infraestructura_masiva(filas):
    """Importacion masiva de un inventario de infraestructura de red externo
    (switches/modems/routers ya escritos a mano en otro archivo). Matchea
    cada fila contra un dispositivo ya existente por IP, si no por MAC, si no
    por N. de Serie -- y si lo encuentra, el archivo manda: sobreescribe los
    campos para los que trae dato (decision de Andres, porque los datos
    cargados a mano antes eran de prueba/incompletos). Si no encuentra
    coincidencia, crea el dispositivo nuevo, infiriendo tipo y plantilla de
    puertos por marca/modelo.
    Cada fila puede traer: ciudad, sucursal (Lugar), piso, observaciones,
    marca, modelo, bocas (texto tipo "24P"), mac, numero_serie, ip, enlace,
    estado.
    Devuelve un resumen {creados, actualizados, total}.
    """
    creados = actualizados = 0
    conn = get_connection()

    for fila in filas:
        ip = (fila.get("ip") or "").strip() or None
        mac = (fila.get("mac") or "").strip() or None
        numero_serie = (fila.get("numero_serie") or "").strip() or None

        existente = None
        if ip:
            existente = conn.execute("SELECT * FROM dispositivos_red WHERE ip = ?", (ip,)).fetchone()
        if not existente and mac:
            existente = conn.execute("SELECT * FROM dispositivos_red WHERE mac = ?", (mac,)).fetchone()
        if not existente and numero_serie:
            existente = conn.execute("SELECT * FROM dispositivos_red WHERE numero_serie = ?", (numero_serie,)).fetchone()

        marca = (fila.get("marca") or "").strip() or None
        modelo = (fila.get("modelo") or "").strip() or None
        bocas_num = _parsear_bocas(fila.get("bocas"))
        observaciones = (fila.get("observaciones") or "").strip()
        observaciones = None if observaciones in ("", "-", "—", "x") else observaciones

        nombre = observaciones or (f"{marca} {modelo}".strip() if (marca or modelo) else None)
        estado_raw = (fila.get("estado") or "").strip().lower()
        estado = _ESTADO_DISPOSITIVO_MAP.get(estado_raw, fila.get("estado") or None)
        tipo, plantilla = _inferir_tipo_y_plantilla(marca, modelo, bocas_num)

        valores = {
            "nombre": nombre, "tipo": tipo, "marca": marca, "modelo": modelo,
            "numero_serie": numero_serie, "cantidad_bocas": bocas_num, "plantilla": plantilla,
            "ip": ip, "mac": mac, "sucursal": (fila.get("sucursal") or "").strip() or None,
            "ciudad": (fila.get("ciudad") or "").strip() or None, "piso": (fila.get("piso") or "").strip() or None,
            "estado": estado, "enlace": (fila.get("enlace") or "").strip() or None,
            "notas": observaciones,
        }

        if existente:
            existente = dict(existente)
            # el archivo manda: solo se mantiene el valor viejo si el archivo no trae nada para ese campo
            for campo, valor in valores.items():
                if valor is None:
                    valores[campo] = existente.get(campo)
            conn.execute(
                """
                UPDATE dispositivos_red
                   SET nombre = ?, tipo = ?, marca = ?, modelo = ?, numero_serie = ?, cantidad_bocas = ?,
                       plantilla = ?, ip = ?, mac = ?, sucursal = ?, ciudad = ?, piso = ?, estado = ?,
                       enlace = ?, notas = ?
                 WHERE id = ?
                """,
                (
                    valores["nombre"], valores["tipo"], valores["marca"], valores["modelo"],
                    valores["numero_serie"], valores["cantidad_bocas"], valores["plantilla"],
                    valores["ip"], valores["mac"], valores["sucursal"], valores["ciudad"],
                    valores["piso"], valores["estado"], valores["enlace"], valores["notas"],
                    existente["id"],
                ),
            )
            actualizados += 1
        else:
            now = datetime.now().isoformat()
            conn.execute(
                """
                INSERT INTO dispositivos_red (
                    nombre, tipo, marca, modelo, numero_serie, cantidad_bocas, plantilla,
                    ip, mac, sucursal, ciudad, piso, estado, enlace, notas, creado_en
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    valores["nombre"] or "Dispositivo sin nombre", valores["tipo"], valores["marca"],
                    valores["modelo"], valores["numero_serie"], valores["cantidad_bocas"], valores["plantilla"],
                    valores["ip"], valores["mac"], valores["sucursal"], valores["ciudad"], valores["piso"],
                    valores["estado"] or "Usado", valores["enlace"], valores["notas"], now,
                ),
            )
            creados += 1

    conn.commit()
    conn.close()
    return {"creados": creados, "actualizados": actualizados, "total": len(filas)}


def assign_puerto(dispositivo_id, puerto, equipo_id):
    """Asigna el equipo indicado a ese puerto del dispositivo (y libera a quien lo tuviera antes).
    Si equipo_id es None, simplemente deja el puerto libre."""
    conn = get_connection()
    conn.execute(
        "UPDATE equipos SET dispositivo_id = NULL, puerto = NULL WHERE dispositivo_id = ? AND puerto = ?",
        (dispositivo_id, puerto),
    )
    if equipo_id:
        conn.execute(
            "UPDATE equipos SET dispositivo_id = ?, puerto = ? WHERE id = ?",
            (dispositivo_id, puerto, equipo_id),
        )
    conn.commit()
    conn.close()


def set_puerto_destino(dispositivo_id, puerto, destino_tipo, destino_id):
    """Asigna a esa boca de un dispositivo su destino, que puede ser un equipo
    (workstation/servidor) o otro dispositivo de red (switch-switch, fortinet-switch, etc).
    Libera cualquier ocupante anterior de esa boca (de cualquiera de los dos tipos).
    destino_tipo: "equipo" | "dispositivo" | "" (deja la boca libre)."""
    conn = get_connection()
    conn.execute(
        "UPDATE equipos SET dispositivo_id = NULL, puerto = NULL WHERE dispositivo_id = ? AND puerto = ?",
        (dispositivo_id, puerto),
    )
    conn.execute(
        "DELETE FROM conexiones_dispositivos WHERE dispositivo_id = ? AND puerto = ?",
        (dispositivo_id, puerto),
    )
    if destino_tipo == "equipo" and destino_id:
        conn.execute(
            "UPDATE equipos SET dispositivo_id = ?, puerto = ? WHERE id = ?",
            (dispositivo_id, puerto, destino_id),
        )
    elif destino_tipo == "dispositivo" and destino_id:
        now = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO conexiones_dispositivos (dispositivo_id, puerto, destino_dispositivo_id, ts) "
            "VALUES (?, ?, ?, ?)",
            (dispositivo_id, puerto, destino_id, now),
        )
    conn.commit()
    conn.close()


def list_conexiones_dispositivos():
    """Devuelve {dispositivo_id: {puerto: destino_dispositivo_id}} para todas las
    conexiones dispositivo-a-dispositivo (switch-switch, fortinet-switch, etc)."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT dispositivo_id, puerto, destino_dispositivo_id FROM conexiones_dispositivos"
    ).fetchall()
    conn.close()
    result = {}
    for r in rows:
        result.setdefault(r["dispositivo_id"], {})[r["puerto"]] = r["destino_dispositivo_id"]
    return result


def list_equipos_export():
    """Todos los equipos con el nombre del dispositivo de red al que estan
    conectados (si tienen), para el export CSV/Excel del inventario."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT e.*, d.nombre AS dispositivo_nombre
        FROM equipos e
        LEFT JOIN dispositivos_red d ON d.id = e.dispositivo_id
        ORDER BY e.ip
        """
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_equipos_por_dispositivo():
    """Devuelve {dispositivo_id: [equipos asignados a ese dispositivo, ordenados por puerto]},
    con los datos necesarios para el panel de detalle del mapa de puertos."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, ip, hostname, mac, marca, modelo, numero_serie, sucursal, notas, "
        "dispositivo_id, puerto, en_linea, responsable, open_ports FROM equipos "
        "WHERE dispositivo_id IS NOT NULL ORDER BY dispositivo_id, puerto"
    ).fetchall()
    conn.close()
    result = {}
    for r in rows:
        result.setdefault(r["dispositivo_id"], []).append(dict(r))
    return result
