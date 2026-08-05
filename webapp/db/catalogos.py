"""Catalogos administrables: departamentos y ciudades (para el directorio de
empleados). Autocontenido."""
from ._core import get_connection


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
