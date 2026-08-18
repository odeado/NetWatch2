"""Conexion SQLite, esquema, y un par de utilidades chicas compartidas por
TODOS los demas submodulos de este paquete (equipos, usuarios, tickets,
etc.) -- lo mas basico de la capa de datos, sin ninguna dependencia hacia
adentro del propio paquete (todo lo demas depende de esto, esto no depende
de nada mas). Ver db/__init__.py para el mapa completo del desglose.
"""
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "netwatch.db"


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
    ip_temporal INTEGER DEFAULT 0,
    notas TEXT,
    os TEXT,
    office TEXT,
    antivirus TEXT,
    categoria TEXT
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
    mascara TEXT,
    sucursal TEXT,
    ciudad TEXT,
    ubicacion TEXT,
    piso TEXT,
    estado TEXT DEFAULT 'Usado',
    fecha_ingreso TEXT,
    enlace TEXT,
    notas TEXT,
    creado_en TEXT,
    en_linea INTEGER,
    fallos_consecutivos INTEGER DEFAULT 0,
    ultima_deteccion TEXT,
    desde TEXT
);

CREATE TABLE IF NOT EXISTS conexiones_dispositivos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dispositivo_id INTEGER NOT NULL,
    puerto TEXT NOT NULL,
    destino_dispositivo_id INTEGER NOT NULL,
    destino_puerto TEXT,
    ts TEXT,
    UNIQUE(dispositivo_id, puerto)
);
"""


def _marca_sync():
    """Timestamp para actualizado_en -- a diferencia del resto de fechas de
    esta base (guardadas en hora local para que se vean naturales en
    pantalla, ej. ultima_deteccion/primera_deteccion/desde), este campo
    SIEMPRE va en UTC con offset explicito. Se compara directo contra el que
    manda la web-admin (JS `new Date().toISOString()`, que tambien es UTC)
    para decidir quien gano la ultima edicion al presionar "Sincronizar con
    la nube" (ver firebase_sync.py) -- si se guardara en hora local, la
    comparacion quedaria corrida por el huso horario del servidor y "quien
    edito mas reciente" podria salir mal."""
    return datetime.now(timezone.utc).isoformat()


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def conexion():
    """Conexion SQLite que se cierra sola al salir del bloque `with`, en
    cualquier camino de salida (return, excepcion, lo que sea) -- antes cada
    funcion de este paquete hacia `conn = get_connection()` ... `conn.close()`
    a mano al final, y una excepcion a mitad de camino (o un early return
    antes del close) dejaba la conexion SQLite abierta para siempre. Usar
    esto en vez de get_connection() directo en toda funcion nueva."""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


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
    if "categoria" not in cols:
        conn.execute("ALTER TABLE equipos ADD COLUMN categoria TEXT")
    if "ip_temporal" not in cols:
        conn.execute("ALTER TABLE equipos ADD COLUMN ip_temporal INTEGER DEFAULT 0")
    if "foto" not in cols:
        conn.execute("ALTER TABLE equipos ADD COLUMN foto TEXT")
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
        if "mascara" not in disp_cols:
            conn.execute("ALTER TABLE dispositivos_red ADD COLUMN mascara TEXT")
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
        if "en_linea" not in disp_cols:
            # NULL a proposito (no 0/1): un dispositivo sin IP, o que nunca
            # cayo dentro de una subred escaneada por el monitor, no debe
            # mostrarse como "Apagado" -- eso seria un falso offline. Solo se
            # setea a 0/1 la primera vez que su IP aparece en un ciclo de
            # escaneo real (ver db.aplicar_estado_red / monitor.py).
            conn.execute("ALTER TABLE dispositivos_red ADD COLUMN en_linea INTEGER")
        if "fallos_consecutivos" not in disp_cols:
            conn.execute("ALTER TABLE dispositivos_red ADD COLUMN fallos_consecutivos INTEGER DEFAULT 0")
        if "ultima_deteccion" not in disp_cols:
            conn.execute("ALTER TABLE dispositivos_red ADD COLUMN ultima_deteccion TEXT")
        if "desde" not in disp_cols:
            conn.execute("ALTER TABLE dispositivos_red ADD COLUMN desde TEXT")
        # Reclasificacion de datos (no de esquema): los modems/ONT (Movistar,
        # Huawei OptiXstar, GPT, etc.) quedaban con tipo "otro" porque ese tipo
        # no existia -- ahora que existe "modem", los movemos una sola vez.
        # Idempotente: una vez reclasificado ya no es tipo='otro', asi que en
        # arranques siguientes esta consulta no vuelve a tocarlos.
        conn.execute(
            """
            UPDATE dispositivos_red SET tipo = 'modem'
             WHERE tipo = 'otro' AND (
                 plantilla = 'ont_router_gpon'
                 OR LOWER(COALESCE(marca, '')) LIKE '%movistar%'
                 OR LOWER(COALESCE(marca, '')) LIKE '%huawei%'
                 OR LOWER(COALESCE(marca, '')) LIKE '%optixstar%'
                 OR LOWER(COALESCE(modelo, '')) LIKE '%ont%'
                 OR LOWER(COALESCE(modelo, '')) LIKE '%gpt%'
                 OR LOWER(COALESCE(modelo, '')) LIKE '%modem%'
             )
            """
        )
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
            destino_puerto TEXT,
            ts TEXT,
            UNIQUE(dispositivo_id, puerto)
        )
    """)
    # migracion suave: bases ya creadas antes de agregar destino_puerto (boca
    # especifica del OTRO switch a la que se conecta esta boca -- antes solo
    # se guardaba "el switch destino" completo, sin decir a que boca de ese
    # switch llegaba el cable).
    conex_cols = {row["name"] for row in conn.execute("PRAGMA table_info(conexiones_dispositivos)")}
    if "destino_puerto" not in conex_cols:
        conn.execute("ALTER TABLE conexiones_dispositivos ADD COLUMN destino_puerto TEXT")
    conn.commit()
    conn.close()
