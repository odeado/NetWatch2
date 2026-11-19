"""Sugerencias revisables de identidad; nunca fusiona automáticamente."""
import json
import re
from datetime import datetime
from ._core import conexion


def motivos(a, b):
    encontrados = []
    mac = re.sub(r'[^0-9a-f]', '', (a.get('mac') or '').lower())
    otra = re.sub(r'[^0-9a-f]', '', (b.get('mac') or '').lower())
    if len(mac) == 12 and mac not in ('000000000000','ffffffffffff') and not (int(mac[:2],16) & 1) and mac == otra:
        encontrados.append('MAC coincidente')
    host = (a.get('hostname') or '').strip().casefold().rstrip('.')
    if len(host) >= 4 and host not in ('localhost','desktop','unknown','windows','sin hostname') and host == (b.get('hostname') or '').strip().casefold().rstrip('.'):
        encontrados.append('Hostname coincidente')
    serie = (a.get('numero_serie') or '').strip().casefold()
    if len(serie) >= 4 and serie not in ('unknown','none','default string','to be filled by o.e.m.','00000000') and serie == (b.get('numero_serie') or '').strip().casefold():
        encontrados.append('Número de serie coincidente')
    return encontrados


def detectar_coincidencias(conn, nuevo_id):
    fila = conn.execute('SELECT * FROM equipos WHERE id=?', (nuevo_id,)).fetchone()
    if not fila or fila['estado_deteccion'] != 'pendiente':
        return
    nuevo = dict(fila)
    for fila in conn.execute("SELECT * FROM equipos WHERE estado_deteccion='confirmado' AND id!=?", (nuevo_id,)):
        conocido = dict(fila)
        razones = motivos(nuevo, conocido)
        if razones:
            conn.execute("""INSERT INTO coincidencias_equipo(conocido_id,nuevo_id,motivos,creado_en)
                VALUES (?,?,?,?) ON CONFLICT(conocido_id,nuevo_id) DO UPDATE SET motivos=excluded.motivos""",
                (conocido['id'],nuevo_id,json.dumps(razones,ensure_ascii=False),datetime.now().isoformat()))


def list_coincidencias_equipo(equipo_id=None):
    with conexion() as conn:
        filas = conn.execute("""SELECT c.*, a.ip AS ip_anterior,a.hostname AS nombre_anterior,
            a.responsable,b.ip AS ip_nueva,b.hostname AS nombre_nuevo,
            a.en_linea AS anterior_online,b.en_linea AS nuevo_online,
            a.sin_datos AS anterior_sin_datos,b.sin_datos AS nuevo_sin_datos
            FROM coincidencias_equipo c JOIN equipos a ON a.id=c.conocido_id
            JOIN equipos b ON b.id=c.nuevo_id WHERE c.estado='pendiente'
            AND a.estado_deteccion='confirmado' AND b.estado_deteccion='pendiente'
            AND (? IS NULL OR c.conocido_id=? OR c.nuevo_id=?) ORDER BY c.creado_en DESC""",
            (equipo_id,equipo_id,equipo_id)).fetchall()
        resultado = []
        for fila in filas:
            r = dict(fila)
            a = dict(conn.execute('SELECT * FROM equipos WHERE id=?',(r['conocido_id'],)).fetchone())
            b = dict(conn.execute('SELECT * FROM equipos WHERE id=?',(r['nuevo_id'],)).fetchone())
            r['motivos'] = motivos(a,b)
            if r['motivos']:
                resultado.append(r)
        return resultado


def ignorar_coincidencia(conocido_id, nuevo_id):
    with conexion() as conn:
        conn.execute("UPDATE coincidencias_equipo SET estado='ignorada' WHERE conocido_id=? AND nuevo_id=?",(conocido_id,nuevo_id))
        conn.commit()
