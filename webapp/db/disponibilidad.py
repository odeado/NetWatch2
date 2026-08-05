"""Eventos recientes y calculo de % de disponibilidad (a partir de la tabla
eventos) -- alimenta tanto la ficha de un equipo como el ranking de
/disponibilidad y las alertas de WhatsApp de equipos criticos caidos.
Autocontenido: solo lee eventos/equipos por SQL directo, sin llamar
funciones de otros submodulos."""
import re
from datetime import datetime, timedelta

from ._core import get_connection


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


_RANKING_IP_RE = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")


def _ranking_clave_orden(orden):
    """Devuelve la funcion de clave de sort para cada criterio soportado por
    /disponibilidad. 'dias' = tiempo que lleva offline: los que estan online
    ahora (sin "desde" en la ventana) quedan al final, no arriba, porque no
    estan "cayendose" en este momento."""
    if orden == "caidas":
        return lambda r: (-r["caidas"], r["pct_online"])
    if orden == "dias":
        def _clave(r):
            if not r["desde"]:
                return (1, "")
            return (0, r["desde"])
        return _clave
    if orden == "ip":
        def _clave(r):
            m = _RANKING_IP_RE.match(r["ip"] or "")
            if m:
                return (0, tuple(int(g) for g in m.groups()))
            return (1, (r["ip"] or "").lower())
        return _clave
    return lambda r: (r["pct_online"], -r["caidas"])  # 'disponibilidad' (default)


def ranking_disponibilidad(dias=30, limite=15, orden="disponibilidad"):
    """Los equipos con peor disponibilidad en los ultimos `dias` dias --
    para encontrar el que anda fallando seguido, no solo el que esta caido
    ahora mismo. Deja afuera los equipos manuales (sin deteccion real) y los
    que no tuvieron ninguna caida en la ventana. `orden` cambia el criterio
    de ordenamiento (ver _ranking_clave_orden): 'disponibilidad' (default,
    peor % primero), 'caidas' (mas caidas primero), 'dias' (lleva mas tiempo
    offline primero) o 'ip'."""
    conn = get_connection()
    equipos = conn.execute(
        "SELECT id, hostname, ip, responsable, sucursal, ciudad, en_linea, primera_deteccion, desde "
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
            # "desde" es cuando entro al estado ACTUAL (ver apply_scan_results) --
            # solo tiene sentido como "lleva caido hace X" cuando esta offline.
            "desde": e["desde"] if not e["en_linea"] else None,
            "pct_online": disp["pct_online"], "caidas": disp["caidas"],
        })
    conn.close()

    resultados.sort(key=_ranking_clave_orden(orden))
    return resultados[:limite]


def equipos_criticos_pendientes_alerta(umbral_minutos=15):
    """Equipos marcados como 'critico', offline hace mas del umbral elegido,
    y a los que todavia no se les mando el aviso para esta caida (para no
    mandar el mismo aviso de nuevo en cada ciclo mientras siga caido).
    Excluye los marcados 'ip_temporal' (equipo bueno usando temporalmente la
    IP de otro que fallo fisicamente) -- ese offline es esperado/conocido
    mientras dura el reemplazo, no una falla real que avisar."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT id, ip, hostname, responsable, sucursal, ciudad, desde
        FROM equipos
        WHERE critico = 1 AND en_linea = 0 AND alerta_offline_enviada = 0
          AND desde IS NOT NULL AND COALESCE(ip_temporal, 0) = 0
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
