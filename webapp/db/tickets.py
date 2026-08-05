"""Tickets de soporte por equipo. Autocontenido, sin dependencias cruzadas
hacia otros submodulos del paquete."""
from datetime import datetime

from ._core import get_connection


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
