"""Equipos: escaneo/estado en linea, ficha tecnica/administrativa, fusion de
duplicados por IP. Es el submodulo mas grande y el que mas de cerca sigue al
scanner (apply_scan_results es el corazon del monitoreo online/offline).

Depende de usuarios.py (get_usuario) para completar responsable/correo al
crear un equipo manual o asignarle responsable -- unica dependencia cruzada
de este submodulo.
"""
import json
from datetime import datetime
from pathlib import Path

from ._core import _marca_sync, get_connection
from .usuarios import get_usuario

FICHA_FIELDS = [
    "hostname", "mac",
    "marca", "modelo", "numero_serie", "fecha_adquisicion", "garantia_hasta",
    "responsable", "correo_responsable", "sucursal", "ciudad", "departamento",
    "cpu", "ram", "almacenamiento", "gpu", "placa_madre",
    "estado_ciclo_vida", "notas", "puerto",
    "os", "office", "antivirus",
    "categoria",
]

CATEGORIAS_EQUIPO = [
    "PC de Escritorio", "Notebook", "All in One", "Servidor",
    "Switch", "Router", "Firewall/UTM", "Access Point", "ONT/Modem",
    "Impresora", "Camara/DVR", "Otro",
]

# De estas categorias para abajo son dispositivos de red/perifericos, no PCs
# -- no tiene sentido mostrarles campos de CPU/RAM/OS/Office/Antivirus (ver
# fieldset-solo-pc en _macros_equipo.html). Los que sean equipos ya
# gestionados de verdad como switch/router deberian vivir en Infraestructura
# (dispositivos_red), pero mientras el escaneo los detecte como "equipo"
# suelto esto evita el desorden de campos que no aplican.
CATEGORIAS_DISPOSITIVO_RED = [
    "Switch", "Router", "Firewall/UTM", "Access Point", "ONT/Modem",
    "Impresora", "Camara/DVR",
]


def list_scan_files(results_dir: Path):
    if not results_dir.exists():
        return []
    return sorted(results_dir.glob("scan_*.json"), reverse=True)


def apply_scan_results(subred, results, source="monitor", offline_after_misses=2, subred_label=None):
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
                # subred_label viene del "label" de config.json (ej. "Arica",
                # "Iquique") -- se guarda como ciudad solo al crear el equipo la
                # primera vez, para que el gauge de resumen muestre un nombre
                # legible en vez del CIDR crudo. No se pisa en updates
                # posteriores por si alguien lo corrigio a mano en la ficha.
                cur = conn.execute(
                    """
                    INSERT INTO equipos (
                        ip, hostname, mac, subred, ciudad, open_ports,
                        confidence_score, confidence_label, metodo_deteccion, estado_deteccion,
                        en_linea, desde, primera_deteccion, ultima_deteccion, ultimo_scan_file
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pendiente', 1, ?, ?, ?, ?)
                    """,
                    (
                        ip, h.get("hostname"), h.get("mac"), subred, subred_label, open_ports_json,
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


def aplicar_reporte_agente(data):
    """Aplica el auto-reporte de tools/agente_inventario.ps1 (datos WMI reales
    del propio PC) haciendo upsert por IP -- mismo patron que apply_scan_results
    (COALESCE para no borrar un dato ya conocido si esta vez el agente no lo
    pudo leer), pero solo sobre campos TECNICOS/de hardware. Los campos
    administrativos (responsable, sucursal, ciudad, departamento, categoria,
    critico, gestionado, notas) nunca se tocan aca -- son del tecnico, no del
    escaneo. Devuelve (equipo_id, es_nuevo)."""
    now = datetime.now().isoformat()
    marca_ts = _marca_sync()
    ip = (data.get("ip") or "").strip()

    def limpio(v):
        if not isinstance(v, str):
            return None
        v = v.strip()
        return v or None

    campos = {
        "hostname": limpio(data.get("hostname")),
        "mac": limpio(data.get("mac")),
        "os": limpio(data.get("os")),
        "marca": limpio(data.get("brand")),
        "modelo": limpio(data.get("model")),
        "numero_serie": limpio(data.get("serial_number")),
        "cpu": limpio(data.get("cpu")),
        "ram": limpio(data.get("ram")),
        "almacenamiento": limpio(data.get("storage")),
        "gpu": limpio(data.get("gpu")),
        "placa_madre": limpio(data.get("motherboard")),
        "office": limpio(data.get("office")),
        "antivirus": limpio(data.get("antivirus")),
    }

    conn = get_connection()
    existing = conn.execute(
        "SELECT id, en_linea FROM equipos WHERE ip = ?", (ip,)
    ).fetchone()

    if existing:
        eq_id = existing["id"]
        was_online = bool(existing["en_linea"])
        conn.execute(
            """
            UPDATE equipos
               SET hostname = COALESCE(?, hostname),
                   mac = COALESCE(?, mac),
                   os = COALESCE(?, os),
                   marca = COALESCE(?, marca),
                   modelo = COALESCE(?, modelo),
                   numero_serie = COALESCE(?, numero_serie),
                   cpu = COALESCE(?, cpu),
                   ram = COALESCE(?, ram),
                   almacenamiento = COALESCE(?, almacenamiento),
                   gpu = COALESCE(?, gpu),
                   placa_madre = COALESCE(?, placa_madre),
                   office = COALESCE(?, office),
                   antivirus = COALESCE(?, antivirus),
                   metodo_deteccion = 'agente',
                   ultima_deteccion = ?,
                   en_linea = 1,
                   fallos_consecutivos = 0,
                   desde = CASE WHEN en_linea = 0 THEN ? ELSE desde END,
                   actualizado_en = ?
             WHERE id = ?
            """,
            (
                campos["hostname"], campos["mac"], campos["os"], campos["marca"], campos["modelo"],
                campos["numero_serie"], campos["cpu"], campos["ram"], campos["almacenamiento"],
                campos["gpu"], campos["placa_madre"], campos["office"], campos["antivirus"],
                now, now, marca_ts, eq_id,
            ),
        )
        if not was_online:
            conn.execute(
                "INSERT INTO eventos (equipo_id, ip, hostname, tipo, ts) VALUES (?, ?, ?, 'online', ?)",
                (eq_id, ip, campos["hostname"], now),
            )
        conn.commit()
        conn.close()
        return eq_id, False

    cur = conn.execute(
        """
        INSERT INTO equipos (
            ip, hostname, mac, os, marca, modelo, numero_serie, cpu, ram, almacenamiento,
            gpu, placa_madre, office, antivirus, metodo_deteccion, estado_deteccion,
            en_linea, desde, primera_deteccion, ultima_deteccion, ultimo_scan_file, actualizado_en
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'agente', 'pendiente', 1, ?, ?, ?, 'agente', ?)
        """,
        (
            ip, campos["hostname"], campos["mac"], campos["os"], campos["marca"], campos["modelo"],
            campos["numero_serie"], campos["cpu"], campos["ram"], campos["almacenamiento"],
            campos["gpu"], campos["placa_madre"], campos["office"], campos["antivirus"],
            now, now, now, marca_ts,
        ),
    )
    eq_id = cur.lastrowid
    conn.execute(
        "INSERT INTO eventos (equipo_id, ip, hostname, tipo, ts) VALUES (?, ?, ?, 'nuevo', ?)",
        (eq_id, ip, campos["hostname"], now),
    )
    conn.commit()
    conn.close()
    return eq_id, True


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


def set_critico(equipo_id, valor):
    """Prende/apaga el flag critico de un equipo sin pasar por el form
    completo de la ficha. Recibe el valor final explicito (no es un toggle
    ciego) para que sea seguro repetir la llamada -- si el boton se llega a
    presionar dos veces rapido (ej. con el servidor mas lento por el escaneo
    de varias subredes), ambas peticiones piden el mismo resultado final en
    vez de cancelarse entre si. Devuelve True si el equipo existia."""
    conn = get_connection()
    cur = conn.execute(
        "UPDATE equipos SET critico = ?, actualizado_en = ? WHERE id = ?",
        (1 if valor else 0, _marca_sync(), equipo_id),
    )
    conn.commit()
    afectado = cur.rowcount > 0
    conn.close()
    return afectado


def update_ficha(equipo_id, fields: dict):
    # actualizado_en se pisa siempre con "ahora" -- es lo que despues usa
    # firebase_sync para decidir quien manda en "Sincronizar con la nube"
    # (gana el lado con la edicion mas reciente, ver _gana_local()). Se hace
    # aca adentro (no en cada llamador) para que TODA edicion de ficha quede
    # cubierta sin acordarse de agregarlo cada vez.
    fields = dict(fields)
    fields["actualizado_en"] = _marca_sync()
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


# Campos que el escaneo llena SOLO (nunca a mano) -- al fusionar, se copian del
# duplicado hacia la ficha que se conserva UNICAMENTE si esta ultima los tiene
# vacios, para no pisar nunca un dato administrativo ya cargado.
_CAMPOS_TECNICOS_FUSION = [
    "hostname", "mac", "open_ports", "confidence_score", "confidence_label",
    "estado_deteccion", "en_linea", "os", "office", "antivirus",
    "metodo_deteccion", "subred",
]


def fusionar_equipo_por_ip(equipo_id, ip_nueva):
    """Un equipo real cambio de IP (ej. le asignaron una IP nueva porque la
    vieja se dio de baja), pero el escaneo ya creo un registro aparte para esa
    IP nueva apenas la vio en la red -- por eso "ya existe" al intentar
    guardar el cambio. Esto fusiona ambos en uno solo: la ficha que se esta
    editando (con su responsable, notas, historial, etc.) adopta la IP nueva
    y se completa con los datos tecnicos que el duplicado ya tenia detectados
    si le faltaban; el duplicado se borra, pero primero se le pasan sus
    tickets/rdp_history/eventos (por si tuviera alguno) para no perder nada.
    Devuelve True si fusiono algo, False si no encontro el duplicado."""
    conn = get_connection()
    dup_row = conn.execute("SELECT * FROM equipos WHERE ip = ?", (ip_nueva,)).fetchone()
    if not dup_row:
        conn.close()
        return False
    duplicado = dict(dup_row)
    dup_id = duplicado["id"]
    if dup_id == equipo_id:
        conn.close()
        return False

    actual_row = conn.execute("SELECT * FROM equipos WHERE id = ?", (equipo_id,)).fetchone()
    if not actual_row:
        conn.close()
        return False
    actual = dict(actual_row)

    updates = {"ip": ip_nueva, "actualizado_en": _marca_sync()}
    for campo in _CAMPOS_TECNICOS_FUSION:
        if not actual.get(campo) and duplicado.get(campo):
            updates[campo] = duplicado[campo]

    # OJO -- el duplicado hay que borrarlo ANTES de ponerle su IP al equipo
    # que se conserva: equipos.ip es UNIQUE, asi que mientras el duplicado
    # siga existiendo con esa IP, el UPDATE de abajo choca contra esa misma
    # restriccion (visto en pruebas: fallaba con UNIQUE constraint failed).
    conn.execute("UPDATE eventos SET equipo_id = ? WHERE equipo_id = ?", (equipo_id, dup_id))
    conn.execute("UPDATE tickets SET equipo_id = ? WHERE equipo_id = ?", (equipo_id, dup_id))
    conn.execute("UPDATE rdp_history SET equipo_id = ? WHERE equipo_id = ?", (equipo_id, dup_id))
    conn.execute("DELETE FROM equipos WHERE id = ?", (dup_id,))

    set_clause = ", ".join(f"{c} = ?" for c in updates)
    conn.execute(f"UPDATE equipos SET {set_clause} WHERE id = ?", list(updates.values()) + [equipo_id])
    conn.commit()
    conn.close()
    return True


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
        "UPDATE equipos SET responsable_id = ?, responsable = ?, correo_responsable = ?, actualizado_en = ? WHERE id = ?",
        (usuario_id, responsable, correo_responsable, _marca_sync(), equipo_id),
    )
    conn.commit()
    conn.close()


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
