"""Importadores masivos que cruzan mas de un dominio (usuarios+equipos, o
necesitan inferencia de dispositivos) -- viven aparte para no forzar a
equipos.py/usuarios.py/dispositivos.py a importarse entre si. Es el unico
submodulo que depende de otros tres del paquete (equipos, usuarios,
dispositivos); nada depende de este."""
import json
from datetime import datetime

from ._core import _clave_nombre, get_connection
from .dispositivos import _ESTADO_DISPOSITIVO_MAP, _inferir_tipo_y_plantilla, _parsear_bocas
from .equipos import get_equipo, get_equipo_by_ip, update_ficha
from .usuarios import find_or_create_usuario_por_nombre


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
