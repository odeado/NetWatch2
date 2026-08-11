"""Topologia (dispositivos de red: switch/router/fortinet/otro, mapeo manual
de puertos). Toca la tabla equipos con SQL directo en un par de lugares
(desvincular al borrar un dispositivo, liberar boca al reasignar) pero no
llama funciones de equipos.py -- sin dependencias cruzadas."""
import re
from datetime import datetime

from ._core import _marca_sync, conexion

TIPOS_DISPOSITIVO = ["switch", "router", "fortinet", "conversor", "modem", "otro"]
TIPO_DISPOSITIVO_LABELS = {
    "switch": "Switch L3",
    "router": "Router",
    "fortinet": "Firewall Fortinet",
    "conversor": "Conversor",
    "modem": "Modem",
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
    "imc_mediachassis_1": {
        "nombre": "IMC Networks MediaChassis/1 (chasis de 1 modulo: cobre + fibra SC)",
        "puertos": [
            {"label": "RJ45", "tipo": "cobre"},
            {"label": "SC 1550/1310", "tipo": "fibra"},
        ],
    },
}


def get_puertos_definicion(d):
    """Devuelve la lista ordenada de bocas [{label, tipo}] de un dispositivo:
    si tiene una plantilla real conocida, usa su layout exacto; si no
    (plantilla "generico" o vacia), arma la grilla numerada a partir de
    cantidad_bocas / bocas_fibra (compatibilidad con dispositivos ya creados)."""
    plantilla = d.get("plantilla") or "generico"
    if plantilla in PLANTILLAS_PUERTOS:
        # OJO: copia cada diccionario de boca (no solo la lista contenedora).
        # PLANTILLAS_PUERTOS es un dict a nivel de modulo compartido por TODOS
        # los dispositivos con la misma plantilla (ej. 5 Fortinets FG-60F). Si
        # solo se copia la lista externa, _dispositivos_con_puertos() termina
        # mutando (p["equipo"] = ...) los MISMOS diccionarios de boca para
        # cada dispositivo, asi que el ultimo dispositivo procesado con esa
        # plantilla pisa el dato de ocupacion de todos los demas -- eso hacia
        # que una asignacion se guardara bien en la base de datos pero se
        # viera "libre" en pantalla si otro Fortinet se procesaba despues.
        return [dict(p) for p in PLANTILLAS_PUERTOS[plantilla]["puertos"]]
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
    with conexion() as conn:
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
        return cur.lastrowid


def list_dispositivos():
    with conexion() as conn:
        rows = conn.execute("SELECT * FROM dispositivos_red ORDER BY sucursal, tipo, nombre").fetchall()
        return [dict(r) for r in rows]


def get_dispositivo(dispositivo_id):
    with conexion() as conn:
        row = conn.execute("SELECT * FROM dispositivos_red WHERE id = ?", (dispositivo_id,)).fetchone()
        return dict(row) if row else None


def update_dispositivo(dispositivo_id, nombre, tipo="switch", marca=None, modelo=None, numero_serie=None,
                       cantidad_bocas=None, bocas_fibra=None, plantilla="generico",
                       ip=None, mac=None, sucursal=None, ciudad=None, ubicacion=None, piso=None,
                       estado="Usado", fecha_ingreso=None, notas=None, enlace=None):
    with conexion() as conn:
        conn.execute(
            """
            UPDATE dispositivos_red
               SET nombre = ?, tipo = ?, marca = ?, modelo = ?, numero_serie = ?, cantidad_bocas = ?,
                   bocas_fibra = ?, plantilla = ?, ip = ?, mac = ?, sucursal = ?, ciudad = ?,
                   ubicacion = ?, piso = ?, estado = ?, fecha_ingreso = ?, notas = ?, enlace = ?,
                   actualizado_en = ?
             WHERE id = ?
            """,
            (nombre, tipo, marca, modelo, numero_serie, cantidad_bocas, bocas_fibra, plantilla,
             ip, mac, sucursal, ciudad, ubicacion, piso, estado, fecha_ingreso, notas, enlace,
             _marca_sync(), dispositivo_id),
        )
        conn.commit()


def eliminar_dispositivo(dispositivo_id):
    """Borra un dispositivo de red (switch/router/fortinet/etc), dejando todo
    limpio para que no queden referencias colgando -- pensado para sacar
    duplicados que quedaron mal cargados en una importacion:
    1) los equipos (PCs) que estaban conectados a una boca de este dispositivo
       quedan sin dispositivo/puerto asignado (no se borran, solo se
       desvinculan).
    2) cualquier conexion switch-a-switch donde este dispositivo era origen O
       destino se elimina, para que la boca del otro lado quede libre en vez
       de mostrar para siempre "ocupado" por un dispositivo que ya no existe.
    3) se borra la fila de dispositivos_red.
    """
    with conexion() as conn:
        conn.execute(
            "UPDATE equipos SET dispositivo_id = NULL, puerto = NULL WHERE dispositivo_id = ?",
            (dispositivo_id,),
        )
        conn.execute("DELETE FROM conexiones_dispositivos WHERE dispositivo_id = ?", (dispositivo_id,))
        conn.execute("DELETE FROM conexiones_dispositivos WHERE destino_dispositivo_id = ?", (dispositivo_id,))
        conn.execute("DELETE FROM dispositivos_red WHERE id = ?", (dispositivo_id,))
        conn.commit()


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
        return "conversor", "conversor_medios"
    if "imc" in marca_l or "mediachassis" in modelo_l:
        return "conversor", "imc_mediachassis_1"
    if ("movistar" in marca_l or "huawei" in marca_l or "gpt" in modelo_l
            or "ont" in modelo_l or "modem" in modelo_l or "optixstar" in marca_l):
        return "modem", "ont_router_gpon"
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


def assign_puerto(dispositivo_id, puerto, equipo_id):
    """Asigna el equipo indicado a ese puerto del dispositivo (y libera a quien lo tuviera antes).
    Si equipo_id es None, simplemente deja el puerto libre."""
    with conexion() as conn:
        conn.execute(
            "UPDATE equipos SET dispositivo_id = NULL, puerto = NULL WHERE dispositivo_id = ? AND puerto = ?",
            (dispositivo_id, puerto),
        )
        if equipo_id:
            conn.execute(
                "UPDATE equipos SET dispositivo_id = ?, puerto = ?, actualizado_en = ? WHERE id = ?",
                (dispositivo_id, puerto, _marca_sync(), equipo_id),
            )
        conn.commit()


def get_destino_dispositivo_anterior(dispositivo_id, puerto):
    """Si esta boca ya estaba conectada a OTRO dispositivo (switch-switch),
    devuelve el id de ese dispositivo destino anterior -- se usa para saber a
    quien mas hay que refrescar en pantalla cuando se reasigna o se libera la
    boca (si no, el otro switch se queda mostrando la boca como ocupada
    aunque ya se desconecto, hasta que alguien recargue toda la pagina)."""
    with conexion() as conn:
        row = conn.execute(
            "SELECT destino_dispositivo_id FROM conexiones_dispositivos WHERE dispositivo_id = ? AND puerto = ?",
            (dispositivo_id, puerto),
        ).fetchone()
        return row["destino_dispositivo_id"] if row else None


def set_puerto_destino(dispositivo_id, puerto, destino_tipo, destino_id, destino_puerto=None):
    """Asigna a esa boca de un dispositivo su destino, que puede ser un equipo
    (workstation/servidor) o otro dispositivo de red (switch-switch, fortinet-switch, etc).
    Libera cualquier ocupante anterior de esa boca (de cualquiera de los dos tipos).
    destino_tipo: "equipo" | "dispositivo" | "" (deja la boca libre).
    destino_puerto: cuando destino_tipo es "dispositivo", la boca ESPECIFICA del otro
    switch a la que llega el cable (ej. conectar la fibra SFP4 de este switch a la
    boca 24 del switch central) -- asi la boca destino tambien queda marcada como
    ocupada del otro lado, en vez de solo anotar "conectado a tal switch" sin decir
    a que boca de ese switch."""
    with conexion() as conn:
        # 1) liberar lo que estuviera antes en ESTA boca (como origen de cualquier tipo)
        conn.execute(
            "UPDATE equipos SET dispositivo_id = NULL, puerto = NULL WHERE dispositivo_id = ? AND puerto = ?",
            (dispositivo_id, puerto),
        )
        conn.execute(
            "DELETE FROM conexiones_dispositivos WHERE dispositivo_id = ? AND puerto = ?",
            (dispositivo_id, puerto),
        )
        # 2) si esta boca era el DESTINO de una conexion de otro dispositivo, tambien se libera
        conn.execute(
            "DELETE FROM conexiones_dispositivos WHERE destino_dispositivo_id = ? AND destino_puerto = ?",
            (dispositivo_id, puerto),
        )

        ahora = _marca_sync()
        if destino_tipo == "equipo" and destino_id:
            conn.execute(
                "UPDATE equipos SET dispositivo_id = ?, puerto = ?, actualizado_en = ? WHERE id = ?",
                (dispositivo_id, puerto, ahora, destino_id),
            )
        elif destino_tipo == "dispositivo" and destino_id and destino_puerto:
            # liberar lo que estuviera antes ocupando la boca DESTINO en el otro switch,
            # para que una misma boca nunca quede con dos ocupantes a la vez.
            conn.execute(
                "UPDATE equipos SET dispositivo_id = NULL, puerto = NULL WHERE dispositivo_id = ? AND puerto = ?",
                (destino_id, destino_puerto),
            )
            conn.execute(
                "DELETE FROM conexiones_dispositivos WHERE dispositivo_id = ? AND puerto = ?",
                (destino_id, destino_puerto),
            )
            conn.execute(
                "DELETE FROM conexiones_dispositivos WHERE destino_dispositivo_id = ? AND destino_puerto = ?",
                (destino_id, destino_puerto),
            )
            now = datetime.now().isoformat()
            conn.execute(
                "INSERT INTO conexiones_dispositivos (dispositivo_id, puerto, destino_dispositivo_id, destino_puerto, ts) "
                "VALUES (?, ?, ?, ?, ?)",
                (dispositivo_id, puerto, destino_id, destino_puerto, now),
            )
        conn.commit()


def list_conexiones_dispositivos():
    """Devuelve todas las conexiones dispositivo-a-dispositivo (switch-switch,
    fortinet-switch, etc) en ambos sentidos, para poder marcar ocupada tanto la
    boca del lado que inicio la conexion como la boca destino especifica en el
    otro dispositivo:
    - origen: {dispositivo_id: {puerto: {"id": destino_dispositivo_id, "puerto_destino": destino_puerto}}}
    - destino: {dispositivo_id: {puerto: {"id": origen_dispositivo_id, "puerto_destino": origen_puerto}}}
    """
    with conexion() as conn:
        rows = conn.execute(
            "SELECT dispositivo_id, puerto, destino_dispositivo_id, destino_puerto FROM conexiones_dispositivos"
        ).fetchall()
    origen = {}
    destino = {}
    for r in rows:
        origen.setdefault(r["dispositivo_id"], {})[r["puerto"]] = {
            "id": r["destino_dispositivo_id"],
            "puerto_destino": r["destino_puerto"],
        }
        if r["destino_puerto"]:
            destino.setdefault(r["destino_dispositivo_id"], {})[r["destino_puerto"]] = {
                "id": r["dispositivo_id"],
                "puerto_destino": r["puerto"],
            }
    return {"origen": origen, "destino": destino}
