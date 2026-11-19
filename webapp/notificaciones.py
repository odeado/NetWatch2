"""Agrupa ráfagas del mismo sitio conservando todos los eventos originales."""
from collections import defaultdict
from datetime import datetime


def agrupar_eventos(eventos, umbral=3):
    grupos = defaultdict(list)
    for e in eventos:
        try:
            ventana = int(datetime.fromisoformat(e['ts'].replace('Z', '+00:00')).timestamp()) // 120
        except (KeyError, ValueError, TypeError):
            ventana = e['id']
        grupos[(e.get('subred'),e.get('tipo'),ventana)].append(e)
    salida = []
    for (subred,tipo,_), grupo in grupos.items():
        if not subred or tipo not in ('online','offline') or len(grupo) < umbral:
            salida.extend(grupo)
            continue
        criticos = [e.get('responsable') or e.get('hostname') or e.get('ip') for e in grupo if e.get('critico')]
        sitio = grupo[-1].get('ciudad') or subred
        salida.append(dict(grupo[-1], equipo_id=None, responsable=None,
            hostname=f'{sitio}: {len(grupo)} equipos', ip='', agrupado=True,
            equipo_ids=[e.get('equipo_id') for e in grupo if e.get('equipo_id')],
            cantidad=len(grupo), criticos=criticos,
            detalle=(f'{len(criticos)} críticos: '+', '.join(criticos[:3])) if criticos else 'Ver equipos de esta red'))
    return sorted(salida, key=lambda e:e['id'])
