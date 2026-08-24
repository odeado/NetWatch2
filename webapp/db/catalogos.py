"""Catalogos administrables: departamentos y ciudades (para el directorio de
empleados). Autocontenido."""
from ._core import conexion


def list_departamentos():
    with conexion() as conn:
        rows = conn.execute("SELECT * FROM departamentos ORDER BY nombre").fetchall()
        return [dict(r) for r in rows]


def create_departamento(nombre):
    with conexion() as conn:
        cur = conn.execute("INSERT OR IGNORE INTO departamentos (nombre) VALUES (?)", (nombre,))
        conn.commit()
        return cur.lastrowid


def delete_departamento(departamento_id):
    with conexion() as conn:
        conn.execute("DELETE FROM departamentos WHERE id = ?", (departamento_id,))
        conn.commit()


def list_ciudades():
    with conexion() as conn:
        rows = conn.execute("SELECT * FROM ciudades ORDER BY nombre").fetchall()
        return [dict(r) for r in rows]


def create_ciudad(nombre):
    with conexion() as conn:
        cur = conn.execute("INSERT OR IGNORE INTO ciudades (nombre) VALUES (?)", (nombre,))
        conn.commit()
        return cur.lastrowid


def delete_ciudad(ciudad_id):
    with conexion() as conn:
        conn.execute("DELETE FROM ciudades WHERE id = ?", (ciudad_id,))
        conn.commit()


def list_subredes():
    with conexion() as conn:
        rows = conn.execute("SELECT * FROM subredes ORDER BY cidr").fetchall()
        return [dict(r) for r in rows]


def create_subred(cidr, ciudad):
    with conexion() as conn:
        cur = conn.execute("INSERT OR IGNORE INTO subredes (cidr, ciudad) VALUES (?, ?)", (cidr, ciudad))
        conn.commit()
        return cur.lastrowid


def update_subred(subred_id, cidr, ciudad):
    with conexion() as conn:
        conn.execute("UPDATE subredes SET cidr = ?, ciudad = ? WHERE id = ?", (cidr, ciudad, subred_id))
        conn.commit()


def delete_subred(subred_id):
    with conexion() as conn:
        conn.execute("DELETE FROM subredes WHERE id = ?", (subred_id,))
        conn.commit()
