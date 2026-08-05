"""Directorio de responsables (usuarios/empleados). No depende de equipos.py
-- las dos operaciones que tocan la tabla equipos (delete_usuario,
delete_usuarios: desvincular al responsable borrado) lo hacen con SQL
directo, no llamando funciones de equipos.py, asi que la dependencia entre
ambos submodulos es de un solo sentido (equipos.py -> usuarios.py, ver
get_usuario ahi)."""
from datetime import datetime

from ._core import _marca_sync, get_connection


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
    ahora = _marca_sync()
    conn = get_connection()
    if actualizar_foto:
        conn.execute(
            """
            UPDATE usuarios
               SET nombre = ?, correo = ?, cargo = ?, sucursal = ?, telefono = ?,
                   foto_perfil = ?, departamento = ?, ciudad = ?, lugar_trabajo = ?,
                   sistemas_autorizados = ?, tipo_vpn = ?, vpn_activa = ?, activo = ?,
                   actualizado_en = ?
             WHERE id = ?
            """,
            (nombre, correo, cargo, sucursal, telefono,
             foto_perfil, departamento, ciudad, lugar_trabajo,
             sistemas_autorizados, tipo_vpn, 1 if vpn_activa else 0, 1 if activo else 0,
             ahora, usuario_id),
        )
    else:
        # no se subio/pego una foto nueva: conserva la que ya tenia
        conn.execute(
            """
            UPDATE usuarios
               SET nombre = ?, correo = ?, cargo = ?, sucursal = ?, telefono = ?,
                   departamento = ?, ciudad = ?, lugar_trabajo = ?,
                   sistemas_autorizados = ?, tipo_vpn = ?, vpn_activa = ?, activo = ?,
                   actualizado_en = ?
             WHERE id = ?
            """,
            (nombre, correo, cargo, sucursal, telefono,
             departamento, ciudad, lugar_trabajo,
             sistemas_autorizados, tipo_vpn, 1 if vpn_activa else 0, 1 if activo else 0,
             ahora, usuario_id),
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


def update_usuario_estado(usuario_id, activo):
    conn = get_connection()
    conn.execute(
        "UPDATE usuarios SET activo = ?, actualizado_en = ? WHERE id = ?",
        (1 if activo else 0, _marca_sync(), usuario_id),
    )
    conn.commit()
    conn.close()
