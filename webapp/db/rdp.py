"""Historial de conexiones RDP. Autocontenido."""
from datetime import datetime

from ._core import get_connection


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
