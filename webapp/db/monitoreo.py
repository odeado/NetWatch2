"""Salud del colector y aplicación transaccional de observaciones únicas."""
from datetime import datetime, timezone
import ipaddress
from ._core import conexion


def instante(valor):
    if not valor:
        return None
    try:
        dt = datetime.fromisoformat(str(valor).replace('Z', '+00:00'))
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.astimezone().astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def estado_agente(reporte, intervalo=120, ahora=None):
    fecha = instante(reporte)
    ahora = ahora or datetime.now(timezone.utc)
    edad = (ahora - fecha).total_seconds() if fecha else None
    limite = max(int(intervalo) * 3, 600)
    if edad is None or edad < -120 or edad > limite:
        return 'sin_datos'
    return 'activo' if edad <= max(int(intervalo) * 1.5, 60) else 'retrasado'


def registrar_revision(sitio, reporte=None, error=None):
    with conexion() as conn:
        anterior = conn.execute('SELECT ultimo_reporte FROM agentes_escaneo WHERE nombre=?',(sitio['nombre'],)).fetchone()
        nuevo = instante(reporte)
        previo = instante(anterior['ultimo_reporte']) if anterior else None
        if nuevo and (nuevo-datetime.now(timezone.utc)).total_seconds() > 120:
            reporte, error = None, 'Reloj del colector adelantado; revisar fecha y zona horaria'
        elif nuevo and previo and nuevo < previo:
            reporte = None
        conn.execute("""INSERT INTO agentes_escaneo
            (nombre,cidr,ciudad,intervalo,ultimo_reporte,ultima_revision,error)
            VALUES (?,?,?,?,?,?,?) ON CONFLICT(nombre) DO UPDATE SET
            cidr=excluded.cidr, ciudad=excluded.ciudad, intervalo=excluded.intervalo,
            ultimo_reporte=COALESCE(excluded.ultimo_reporte,agentes_escaneo.ultimo_reporte),
            ultima_revision=excluded.ultima_revision,error=excluded.error""",
            (sitio['nombre'], sitio['cidr'], sitio.get('ciudad') or sitio['nombre'],
             sitio.get('revisar_cada_seg',120), reporte,
             datetime.now(timezone.utc).isoformat(), error))
        conn.commit()


def list_agentes_escaneo():
    with conexion() as conn:
        agentes = [dict(r) for r in conn.execute('SELECT * FROM agentes_escaneo ORDER BY nombre')]
    for a in agentes:
        a['estado'] = estado_agente(a['ultimo_reporte'], a['intervalo'])
        if a['error'] and a['estado'] == 'activo':
            a['estado'] = 'retrasado'
        revision = instante(a['ultima_revision'])
        if revision is None or (datetime.now(timezone.utc)-revision).total_seconds() > max(a['intervalo']*3,600):
            a['estado'] = 'sin_datos'
    return agentes


def marcar_subred_sin_datos(cidr):
    red = ipaddress.ip_network(cidr, strict=False)
    now = datetime.now().isoformat()
    with conexion() as conn:
        rows = conn.execute("SELECT id,en_linea,sin_datos FROM equipos WHERE subred=? AND origen!='manual'", (cidr,)).fetchall()
        for e in rows:
            if not e['sin_datos']:
                conn.execute("INSERT INTO eventos(equipo_id,tipo,ts) VALUES (?,'sin_datos',?)", (e['id'],now))
        conn.execute("UPDATE equipos SET sin_datos=1,fallos_consecutivos=0 WHERE subred=? AND origen!='manual'", (cidr,))
        for d in conn.execute("SELECT id,ip FROM dispositivos_red WHERE ip IS NOT NULL"):
            try:
                pertenece = ipaddress.ip_address(d['ip']) in red
            except ValueError:
                pertenece = False
            if pertenece:
                conn.execute('UPDATE dispositivos_red SET sin_datos=1,fallos_consecutivos=0 WHERE id=?', (d['id'],))
        conn.commit()


def aplicar_escaneo_remoto(sitio, resultados, scan_id, respaldo=False):
    """Equipo, infraestructura y cursor se confirman en una sola transacción.
    Repetir un payload o recibir uno anterior nunca suma otro fallo."""
    from .equipos import apply_scan_results
    from .dispositivos import aplicar_estado_red
    with conexion() as conn:
        conn.execute('BEGIN IMMEDIATE')
        fila = conn.execute('SELECT ultimo_scan_id FROM agentes_escaneo WHERE nombre=?', (sitio['nombre'],)).fetchone()
        actual = instante(scan_id)
        anterior = instante(fila['ultimo_scan_id']) if fila else None
        if not actual or (not respaldo and anterior and actual <= anterior):
            return None
        eventos = apply_scan_results(sitio['cidr'], resultados,
            source=('respaldo:' if respaldo else 'firebase:')+sitio['nombre'],
            offline_after_misses=sitio.get('offline_after_misses',2),
            subred_label=sitio.get('ciudad'), conn=conn,
            observado_en=actual.astimezone().replace(tzinfo=None).isoformat())
        aplicar_estado_red(resultados, offline_after_misses=sitio.get('offline_after_misses',2), conn=conn)
        campo = 'ultimo_respaldo' if respaldo else 'ultimo_scan_id'
        conn.execute(f'UPDATE agentes_escaneo SET {campo}=? WHERE nombre=?', (actual.isoformat(),sitio['nombre']))
        conn.commit()
        return eventos


def anotar_salud_equipos(equipos):
    """La interfaz también envejece los datos si el monitor central se detiene."""
    agentes = {a['cidr']:a for a in list_agentes_escaneo()}
    for e in equipos:
        a = agentes.get(e.get('subred'))
        if not a or e.get('origen')=='manual':
            continue
        fuente = e.get('ultimo_scan_file') or ''
        if fuente.startswith(('firebase:','respaldo:')) and a['estado']=='sin_datos':
            respaldo = instante(a.get('ultimo_respaldo'))
            vigente = respaldo and (datetime.now(timezone.utc)-respaldo).total_seconds() <= max(a['intervalo']*3,600)
            if not vigente:
                e['sin_datos'] = 1
    return equipos
