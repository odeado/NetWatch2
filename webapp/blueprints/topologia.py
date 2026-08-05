"""Topologia/Infraestructura de red -- cuarto modulo movido fuera de app.py
(ver blueprints/disponibilidad.py para el patron). El mas grande hasta
ahora: 2 helpers de armado de datos, el diagrama de flujo (el codigo mas
denso de toda la app, ver _construir_componentes) y el CRUD de
dispositivos/puertos.

OJO al moverlo: los 8 endpoints pasan a llamarse "topologia.<vista>" (ej.
topologia.topologia, topologia.topologia_diagrama) -- se actualizaron los
url_for() en admin.html, admin_equipos.html, admin_parametros.html,
topologia.html, topologia_diagrama.html y topologia_resumen.html. La unica
referencia que NO se toca es el fetch de asignar_puerto en topologia.html
(linea ~473): esa arma la URL a mano con JS ("/topologia/dispositivos/" +
id + "/puertos"), no usa url_for, y la ruta en si no cambio -- solo el
nombre interno del endpoint.
"""
import json
from collections import deque
from datetime import datetime

from flask import Blueprint, jsonify, redirect, render_template, request, url_for

import db
from parsers import _parsear_infraestructura_html

bp = Blueprint("topologia", __name__)


def _dispositivos_con_puertos():
    """Arma la lista de dispositivos de red con sus puertos ya resueltos
    (que equipo o que otro dispositivo tiene conectado cada boca). Comparten
    esta logica tanto la vista interactiva de Topologia como el resumen
    imprimible."""
    dispositivos = db.list_dispositivos()
    equipos_por_dispositivo = db.list_equipos_por_dispositivo()
    conexiones = db.list_conexiones_dispositivos()
    conexiones_origen = conexiones["origen"]
    conexiones_destino = conexiones["destino"]
    dispositivos_by_id = {d["id"]: d for d in dispositivos}

    for lista_eq in equipos_por_dispositivo.values():
        for eq in lista_eq:
            try:
                puertos_abiertos = json.loads(eq.get("open_ports") or "[]")
            except (TypeError, ValueError):
                puertos_abiertos = []
            eq["has_rdp"] = any(p.get("port") == 3389 for p in puertos_abiertos)

    for d in dispositivos:
        ocupados_equipo = {
            eq["puerto"]: eq
            for eq in equipos_por_dispositivo.get(d["id"], [])
            if eq.get("puerto")
        }
        # una boca puede estar ocupada porque ESTE dispositivo inicio la conexion
        # hacia otro switch, o porque OTRO dispositivo eligio esta boca especifica
        # como destino -- ambos casos la dejan ocupada por igual.
        ocupados_dispositivo = dict(conexiones_origen.get(d["id"], {}))
        for puerto, info in conexiones_destino.get(d["id"], {}).items():
            ocupados_dispositivo.setdefault(puerto, info)

        puertos = db.get_puertos_definicion(d)
        for p in puertos:
            p["equipo"] = ocupados_equipo.pop(p["label"], None)
            destino_info = ocupados_dispositivo.pop(p["label"], None)
            destino_disp = dispositivos_by_id.get(destino_info["id"]) if destino_info else None
            # OJO: nunca guardar aca el dict COMPLETO del otro dispositivo (ese
            # mismo dict, mas abajo en su propia vuelta del for, recibe su
            # propia clave "puertos"). Si dos switches se apuntan uno al otro
            # (un cable real switch-switch, cada lado con su propia boca
            # asignada), guardar el objeto completo arma una referencia
            # circular (A -> puertos -> destino B -> puertos -> destino A -> ...)
            # que json.dumps/tojson no puede serializar ("Circular reference
            # detected") y rompe toda la pagina de Infraestructura. Por eso se
            # copian aca solo los campos que la pantalla realmente necesita
            # mostrar, en un dict nuevo y plano.
            p["dispositivo_destino"] = {
                "id": destino_disp["id"],
                "nombre": destino_disp["nombre"],
                "tipo": destino_disp.get("tipo"),
                "marca": destino_disp.get("marca"),
                "modelo": destino_disp.get("modelo"),
                "ip": destino_disp.get("ip"),
                "numero_serie": destino_disp.get("numero_serie"),
                "mac": destino_disp.get("mac"),
                "enlace": destino_disp.get("enlace"),
            } if destino_disp else None
            p["dispositivo_destino_puerto"] = destino_info["puerto_destino"] if destino_info else None
        d["puertos"] = puertos
        # equipos con un puerto asignado que no calza con la grilla de bocas definida
        # (ej. si todavia no se configuro plantilla/bocas, o texto libre viejo)
        d["puertos_fuera_de_grilla"] = list(ocupados_equipo.values())
        # resumen "boca usadas / total" para el listado de Infraestructura, asi
        # se ve de un vistazo que switches ya estan llenos y cuales tienen
        # espacio -- sin tener que abrir "Editar puertos" uno por uno.
        d["puertos_ocupados"] = sum(1 for p in puertos if p.get("equipo") or p.get("dispositivo_destino"))
        d["puertos_total"] = len(puertos)

    return dispositivos


def _agrupar_por_ciudad_sucursal(dispositivos):
    """Agrupa los dispositivos en banners Ciudad -> Sucursal, en el mismo
    orden en que aparecen (ya vienen ordenados por sucursal/tipo/nombre desde
    db.list_dispositivos()), para que la tabla de Infraestructura se pueda
    recorrer de un vistazo en vez de una lista plana de 40+ filas."""
    ciudades = {}
    orden_ciudades = []
    for d in dispositivos:
        ciudad = d.get("ciudad") or "Sin ciudad asignada"
        sucursal = d.get("sucursal") or "Sin sucursal asignada"
        if ciudad not in ciudades:
            ciudades[ciudad] = {}
            orden_ciudades.append(ciudad)
        if sucursal not in ciudades[ciudad]:
            ciudades[ciudad][sucursal] = []
        ciudades[ciudad][sucursal].append(d)

    grupos = []
    for ciudad in orden_ciudades:
        sucursales = [
            {"sucursal": sucursal, "dispositivos": disps, "total": len(disps)}
            for sucursal, disps in ciudades[ciudad].items()
        ]
        total_ciudad = sum(s["total"] for s in sucursales)
        grupos.append({"ciudad": ciudad, "sucursales": sucursales, "total": total_ciudad})
    return grupos


@bp.route("/topologia")
def topologia():
    dispositivos = _dispositivos_con_puertos()

    resumen_importacion = None
    if request.args.get("importado") == "1":
        resumen_importacion = {
            "creados": int(request.args.get("creados", 0)),
            "actualizados": int(request.args.get("actualizados", 0)),
            "total": int(request.args.get("total", 0)),
        }

    return render_template(
        "topologia.html",
        dispositivos=dispositivos,
        grupos_topologia=_agrupar_por_ciudad_sucursal(dispositivos),
        tipos=db.TIPOS_DISPOSITIVO,
        tipo_labels=db.TIPO_DISPOSITIVO_LABELS,
        estados=db.ESTADOS_DISPOSITIVO,
        plantillas=db.PLANTILLAS_PUERTOS,
        equipos=db.list_equipos(),
        ciudades=db.list_ciudades(),
        active_tab="infraestructura",
        resumen_importacion=resumen_importacion,
        error=request.args.get("error"),
    )


@bp.route("/topologia/importar", methods=["POST"])
def importar_infraestructura():
    """Importa un inventario de infraestructura externo (switches/modems/
    routers ya escritos a mano en otro archivo, guardado como tabla HTML con
    extension .xls). Matchea por IP/MAC/N.Serie; si encuentra el dispositivo
    el archivo manda, si no lo crea infiriendo tipo y plantilla de puertos."""
    archivo = request.files.get("archivo")
    if not archivo or not archivo.filename:
        return redirect(url_for("topologia.topologia", error="archivo_requerido"))

    contenido = archivo.read().decode("utf-8", errors="replace")
    filas = _parsear_infraestructura_html(contenido)
    if not filas:
        return redirect(url_for("topologia.topologia", error="archivo_sin_filas"))

    resumen = db.importar_infraestructura_masiva(filas)
    return redirect(url_for(
        "topologia.topologia", importado="1",
        creados=resumen["creados"], actualizados=resumen["actualizados"], total=resumen["total"],
    ))


@bp.route("/topologia/resumen")
def topologia_resumen():
    """Resumen imprimible de la red: por cada dispositivo, sus datos y que
    hay conectado en cada boca ocupada. Pensado para Ctrl+P / Guardar como
    PDF, no para editar nada."""
    dispositivos = _dispositivos_con_puertos()
    for d in dispositivos:
        d["puertos_ocupados"] = [
            p for p in d["puertos"] if p.get("equipo") or p.get("dispositivo_destino")
        ]
        d["puertos_libres_count"] = len(d["puertos"]) - len(d["puertos_ocupados"])

    grupos = {}
    for d in dispositivos:
        clave = d.get("ciudad") or "Sin ciudad asignada"
        grupos.setdefault(clave, []).append(d)

    return render_template(
        "topologia_resumen.html",
        grupos=grupos,
        tipo_labels=db.TIPO_DISPOSITIVO_LABELS,
        generado_en=datetime.now().strftime("%d-%m-%Y %H:%M"),
    )


TIPO_DISPOSITIVO_COLOR = {
    "switch": "#60a5fa",
    "router": "#a78bfa",
    "fortinet": "#fb923c",
    "conversor": "#2dd4bf",
    "modem": "#fbbf24",
    "otro": "#9aa3b8",
}
TIPO_DISPOSITIVO_SIGLA = {
    "switch": "SW",
    "router": "RT",
    "fortinet": "FW",
    "conversor": "CV",
    "modem": "MD",
    "otro": "?",
}
# Orden de prioridad para elegir la "raiz" visual de cada cadena conectada en el
# diagrama de flujo (de donde empieza la columna 0 hacia la derecha): se prefiere
# arrancar desde el equipo mas "aguas arriba" de la red (firewall/router), y que
# los switches/modems/conversores queden aguas abajo, en vez de un orden
# arbitrario que hacia que las lineas de conexion salieran para cualquier lado.
TIPO_PRIORIDAD_RAIZ = {"fortinet": 0, "router": 1, "switch": 2, "modem": 3, "conversor": 4, "otro": 5}


def _anchor_puerto(nodo, otro):
    """Punto de anclaje en el borde del nodo mas cercano al otro nodo, para
    que las lineas de conexion salgan del borde de la caja y no de su centro."""
    cx, cy = nodo["x"] + nodo["w"] / 2, nodo["y"] + nodo["h"] / 2
    ocx, ocy = otro["x"] + otro["w"] / 2, otro["y"] + otro["h"] / 2
    dx, dy = ocx - cx, ocy - cy
    if abs(dy) >= abs(dx):
        return cx, (nodo["y"] + nodo["h"] if dy > 0 else nodo["y"])
    return (nodo["x"] + nodo["w"] if dx > 0 else nodo["x"]), cy


def _construir_componentes(disps, adyacencia):
    """Divide una lista de dispositivos en "componentes" (grupos que estan
    fisicamente conectados entre si por conexiones_dispositivos), y dentro de
    cada componente calcula la profundidad BFS de cada dispositivo desde una
    raiz elegida por tipo (ver TIPO_PRIORIDAD_RAIZ).

    Antes el diagrama ponia TODOS los dispositivos de una ciudad en una sola
    fila, en el orden en que salian de la base de datos -- sin importar si
    estaban conectados entre si o no. Con equipos sin ninguna relacion
    quedando pegados unos a otros, las lineas de conexion terminaban cruzando
    todo el dibujo de cualquier manera ("todo derecho hacia un lado"). Esta
    funcion arma en cambio, por cada grupo de dispositivos realmente
    conectados, una cadena ordenada izquierda-a-derecha (columna = saltos de
    distancia desde la raiz), para que el dibujo se lea como un flujo real de
    la red en vez de una lista sin orden. Los dispositivos sueltos (sin
    ninguna conexion a otro dispositivo) quedan cada uno como su propio
    "componente" de un solo nodo en columna 0."""
    ids_en_grupo = {d["id"] for d in disps}
    por_id = {d["id"]: d for d in disps}
    visitados = set()
    componentes = []

    for d in disps:
        if d["id"] in visitados:
            continue
        comp_ids = {d["id"]}
        visitados.add(d["id"])
        cola = deque([d["id"]])
        while cola:
            actual = cola.popleft()
            for vecino in adyacencia.get(actual, ()):
                if vecino in ids_en_grupo and vecino not in visitados:
                    visitados.add(vecino)
                    comp_ids.add(vecino)
                    cola.append(vecino)

        comp_disps = [por_id[i] for i in comp_ids]
        raiz = min(comp_disps, key=lambda x: (TIPO_PRIORIDAD_RAIZ.get(x.get("tipo"), 9), x["nombre"] or ""))

        profundidad = {raiz["id"]: 0}
        cola = deque([raiz["id"]])
        while cola:
            actual = cola.popleft()
            for vecino in adyacencia.get(actual, ()):
                if vecino in comp_ids and vecino not in profundidad:
                    profundidad[vecino] = profundidad[actual] + 1
                    cola.append(vecino)
        for cid in comp_ids:
            profundidad.setdefault(cid, 0)

        componentes.append({"disps": comp_disps, "profundidad": profundidad})

    return componentes


@bp.route("/topologia/diagrama")
def topologia_diagrama():
    """Diagrama de flujo de la red: cada dispositivo como caja con su mini
    salud de equipos conectados, unidos por lineas curvas segun las conexiones
    dispositivo-a-dispositivo ya cargadas. Para cuando pidan mostrar como esta
    armada la red de un vistazo. Con el boton "Ver equipos conectados" se
    despliega ademas el detalle (PC/impresora/etc) de cada equipo colgado de
    esa boca, y al hacer clic en un dispositivo se abre un panel con sus
    datos y enlaces. El selector de ciudad filtra que se dibuja/imprime."""
    todos_dispositivos = _dispositivos_con_puertos()
    todas_ciudades = sorted({d.get("ciudad") or "Sin ciudad asignada" for d in todos_dispositivos})
    # combos ciudad+sucursal para el filtro "Subred" -- mas fino que el de
    # ciudad sola, para poder mirar solo un local puntual (ej. "Antofagasta
    # Matta - Comercial") en vez de que salgan mezclados todos los dispositivos
    # de toda la ciudad, incluidos los que estan Fuera de servicio en otra
    # sucursal sin ninguna relacion.
    combos_vistos = set()
    todas_subredes = []
    for d in sorted(todos_dispositivos, key=lambda x: ((x.get("ciudad") or "Sin ciudad asignada"), x.get("sucursal") or "")):
        ciudad_d = d.get("ciudad") or "Sin ciudad asignada"
        sucursal_d = d.get("sucursal") or ""
        combo = (ciudad_d, sucursal_d)
        if combo in combos_vistos:
            continue
        combos_vistos.add(combo)
        todas_subredes.append({
            "valor": f"{ciudad_d}::{sucursal_d}",
            "etiqueta": f"{ciudad_d} {sucursal_d}".strip() if sucursal_d else ciudad_d,
        })

    ciudad_filtro = request.args.get("ciudad", "todos")
    subred_filtro = request.args.get("subred", "todos")

    if subred_filtro and subred_filtro != "todos" and "::" in subred_filtro:
        ciudad_sub, sucursal_sub = subred_filtro.split("::", 1)
        dispositivos = [
            d for d in todos_dispositivos
            if (d.get("ciudad") or "Sin ciudad asignada") == ciudad_sub
            and (d.get("sucursal") or "") == sucursal_sub
        ]
    elif ciudad_filtro and ciudad_filtro != "todos":
        dispositivos = [
            d for d in todos_dispositivos
            if (d.get("ciudad") or "Sin ciudad asignada") == ciudad_filtro
        ]
    else:
        dispositivos = todos_dispositivos

    # agrupar por Ciudad + Sucursal (no solo ciudad): asi cada recuadro
    # punteado representa un local fisico real, que es la unidad natural en la
    # que los dispositivos estan realmente cableados entre si.
    grupos = {}
    orden_grupos = []
    for d in dispositivos:
        clave = (d.get("ciudad") or "Sin ciudad asignada", d.get("sucursal") or None)
        if clave not in grupos:
            grupos[clave] = []
            orden_grupos.append(clave)
        grupos[clave].append(d)

    # adyacencia dispositivo-a-dispositivo (grafo no dirigido) de TODO lo que
    # esta visible con el filtro actual, para poder ordenar cada grupo segun
    # como estan conectados de verdad entre si, en vez de por orden alfabetico.
    dispositivos_by_id = {d["id"]: d for d in dispositivos}
    adyacencia = {d["id"]: set() for d in dispositivos}
    for d in dispositivos:
        for p in d["puertos"]:
            destino = p.get("dispositivo_destino")
            if destino and destino["id"] in dispositivos_by_id:
                adyacencia[d["id"]].add(destino["id"])
                adyacencia[destino["id"]].add(d["id"])

    node_w, node_h = 200, 118
    col_gap, row_gap = 110, 90
    margin = 50
    grupo_pad = 26
    leaf_h, leaf_gap = 24, 6
    drawer_top_pad = 14

    def _extra_h(cantidad):
        if not cantidad:
            return 0
        return drawer_top_pad + cantidad * (leaf_h + leaf_gap) - leaf_gap + 12

    def _armar_nodo(d, ciudad):
        equipos_conectados = [p["equipo"] for p in d["puertos"] if p.get("equipo")]
        equipos_conectados.sort(key=lambda e: bool(e.get("en_linea")))
        equipos_detalle = [{
            "hostname": e.get("hostname") or e.get("ip"),
            "ip": e.get("ip"),
            "en_linea": bool(e.get("en_linea")),
            "has_rdp": bool(e.get("has_rdp")),
            "responsable": e.get("responsable"),
        } for e in equipos_conectados]
        return {
            "id": d["id"],
            "nombre": d["nombre"],
            "tipo": d.get("tipo"),
            "color": TIPO_DISPOSITIVO_COLOR.get(d.get("tipo"), "#9aa3b8"),
            "sigla": TIPO_DISPOSITIVO_SIGLA.get(d.get("tipo"), "?"),
            "ip": d.get("ip"),
            "mac": d.get("mac"),
            "numero_serie": d.get("numero_serie"),
            "estado": d.get("estado"),
            "sucursal": d.get("sucursal"),
            "piso": d.get("piso"),
            "notas": d.get("notas"),
            "ciudad": ciudad,
            "w": node_w,
            "h": node_h,
            "equipos_total": len(equipos_conectados),
            "equipos_offline": sum(1 for e in equipos_conectados if not e.get("en_linea")),
            "equipos_dots": equipos_conectados[:20],
            "equipos_extra": max(0, len(equipos_conectados) - 20),
            "equipos_detalle": equipos_detalle,
            "enlaces_detalle": [],
        }

    nodos = {}
    grupos_layout = []
    ancho_max_grupo = 0
    y_cursor = margin

    for (ciudad, sucursal) in orden_grupos:
        disps = grupos[(ciudad, sucursal)]
        # cada componente = un grupo de dispositivos realmente conectados
        # entre si dentro de este local; se dibujan como una cadena
        # ordenada izquierda-a-derecha segun la distancia (en saltos) desde
        # la raiz elegida, en vez de amontonar todo en una sola fila.
        componentes = _construir_componentes(disps, adyacencia)
        componentes.sort(key=lambda c: (-len(c["disps"]), (c["disps"][0].get("nombre") or "")))

        grupo_y_caja = y_cursor
        fila_y = grupo_y_caja + grupo_pad
        grupo_ancho = 0

        for idx_comp, comp in enumerate(componentes):
            por_columna = {}
            for dd in comp["disps"]:
                por_columna.setdefault(comp["profundidad"][dd["id"]], []).append(dd)
            for col in por_columna:
                por_columna[col].sort(key=lambda x: (x.get("nombre") or ""))

            max_col = max(por_columna) if por_columna else 0
            max_filas = max((len(v) for v in por_columna.values()), default=1)
            comp_ancho = (max_col + 1) * node_w + max_col * col_gap
            grupo_ancho = max(grupo_ancho, comp_ancho)

            comp_extra_h = 0
            for col, disps_en_col in por_columna.items():
                x = margin + grupo_pad + col * (node_w + col_gap)
                for row_idx, dd in enumerate(disps_en_col):
                    nodo = _armar_nodo(dd, ciudad)
                    nodo["x"] = x
                    nodo["y"] = fila_y + row_idx * (node_h + row_gap)
                    nodo["drawer_y"] = nodo["y"] + node_h + drawer_top_pad
                    nodos[dd["id"]] = nodo
                    comp_extra_h = max(comp_extra_h, _extra_h(len(nodo["equipos_detalle"])))

            comp_h = max_filas * node_h + (max_filas - 1) * row_gap + comp_extra_h
            fila_y += comp_h
            if idx_comp < len(componentes) - 1:
                fila_y += row_gap

        grupo_h = (fila_y - grupo_y_caja) + grupo_pad
        grupo_w = grupo_ancho + grupo_pad * 2
        nombre_grupo = f"{ciudad} - {sucursal}" if sucursal else ciudad
        grupos_layout.append({"nombre": nombre_grupo, "x": margin, "y": grupo_y_caja, "w": grupo_w, "h": grupo_h})
        ancho_max_grupo = max(ancho_max_grupo, grupo_w)
        y_cursor = grupo_y_caja + grupo_h + row_gap * 1.5

    # lineas dispositivo-a-dispositivo, sin duplicar el mismo par en ambos sentidos
    vistos = set()
    enlaces = []
    for d in dispositivos:
        for p in d["puertos"]:
            destino = p.get("dispositivo_destino")
            if not destino or destino["id"] not in nodos:
                continue
            par = tuple(sorted((d["id"], destino["id"])))
            if par in vistos:
                continue
            vistos.add(par)
            origen_n, destino_n = nodos[d["id"]], nodos[destino["id"]]
            origen_n["enlaces_detalle"].append({
                "puerto_local": p["label"], "otro_nombre": destino_n["nombre"],
                "otro_ip": destino_n.get("ip"), "otro_tipo": destino_n.get("tipo"),
            })
            destino_n["enlaces_detalle"].append({
                "puerto_local": "N/D", "otro_nombre": origen_n["nombre"],
                "otro_ip": origen_n.get("ip"), "otro_tipo": origen_n.get("tipo"),
            })
            x1, y1 = _anchor_puerto(origen_n, destino_n)
            x2, y2 = _anchor_puerto(destino_n, origen_n)
            vertical = abs(y2 - y1) >= abs(x2 - x1)
            if vertical:
                c1x, c1y = x1, y1 + (y2 - y1) / 2
                c2x, c2y = x2, y1 + (y2 - y1) / 2
            else:
                c1x, c1y = x1 + (x2 - x1) / 2, y1
                c2x, c2y = x1 + (x2 - x1) / 2, y2
            enlaces.append({
                "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                "c1x": c1x, "c1y": c1y, "c2x": c2x, "c2y": c2y,
                "puerto_origen": p["label"],
            })

    ancho = max(ancho_max_grupo + margin * 2, 560)
    alto = y_cursor + margin

    return render_template(
        "topologia_diagrama.html",
        nodos=list(nodos.values()),
        grupos_layout=grupos_layout,
        enlaces=enlaces,
        tipo_labels=db.TIPO_DISPOSITIVO_LABELS,
        ancho=ancho,
        alto=alto,
        generado_en=datetime.now().strftime("%d-%m-%Y %H:%M"),
        todas_ciudades=todas_ciudades,
        ciudad_filtro=ciudad_filtro,
        todas_subredes=todas_subredes,
        subred_filtro=subred_filtro,
    )


def _int_from_form(field):
    raw = request.form.get(field, "").strip()
    return int(raw) if raw.isdigit() else None


@bp.route("/topologia/dispositivos", methods=["POST"])
def crear_dispositivo():
    nombre = request.form.get("nombre", "").strip()
    if nombre:
        db.create_dispositivo(
            nombre,
            request.form.get("tipo", "switch"),
            request.form.get("marca", "").strip() or None,
            request.form.get("modelo", "").strip() or None,
            request.form.get("numero_serie", "").strip() or None,
            _int_from_form("cantidad_bocas"),
            _int_from_form("bocas_fibra"),
            request.form.get("plantilla", "generico"),
            request.form.get("ip", "").strip() or None,
            request.form.get("mac", "").strip() or None,
            request.form.get("sucursal", "").strip() or None,
            request.form.get("ciudad", "").strip() or None,
            request.form.get("ubicacion", "").strip() or None,
            request.form.get("piso", "").strip() or None,
            request.form.get("estado", "Usado"),
            request.form.get("fecha_ingreso", "").strip() or None,
            request.form.get("notas", "").strip() or None,
            request.form.get("enlace", "").strip() or None,
        )
    return redirect(url_for("topologia.topologia"))


@bp.route("/topologia/dispositivos/<int:dispositivo_id>", methods=["POST"])
def editar_dispositivo(dispositivo_id):
    db.update_dispositivo(
        dispositivo_id,
        request.form.get("nombre", "").strip(),
        request.form.get("tipo", "switch"),
        request.form.get("marca", "").strip() or None,
        request.form.get("modelo", "").strip() or None,
        request.form.get("numero_serie", "").strip() or None,
        _int_from_form("cantidad_bocas"),
        _int_from_form("bocas_fibra"),
        request.form.get("plantilla", "generico"),
        request.form.get("ip", "").strip() or None,
        request.form.get("mac", "").strip() or None,
        request.form.get("sucursal", "").strip() or None,
        request.form.get("ciudad", "").strip() or None,
        request.form.get("ubicacion", "").strip() or None,
        request.form.get("piso", "").strip() or None,
        request.form.get("estado", "Usado"),
        request.form.get("fecha_ingreso", "").strip() or None,
        request.form.get("notas", "").strip() or None,
        request.form.get("enlace", "").strip() or None,
    )
    return redirect(url_for("topologia.topologia"))


@bp.route("/topologia/dispositivos/<int:dispositivo_id>/eliminar", methods=["POST"])
def eliminar_dispositivo(dispositivo_id):
    db.eliminar_dispositivo(dispositivo_id)
    return redirect(url_for("topologia.topologia"))


@bp.route("/topologia/dispositivos/<int:dispositivo_id>/puertos", methods=["POST"])
def asignar_puerto(dispositivo_id):
    puerto = request.form.get("puerto", "").strip()
    destino = request.form.get("destino", "")
    destino_puerto = request.form.get("destino_puerto", "").strip() or None
    destino_tipo, destino_id = "", None
    if destino.startswith("equipo:"):
        destino_tipo, destino_id = "equipo", int(destino.split(":", 1)[1])
    elif destino.startswith("dispositivo:"):
        destino_tipo, destino_id = "dispositivo", int(destino.split(":", 1)[1])

    # Ademas de este dispositivo, hay que refrescar tambien: el switch que
    # quedaba conectado ANTES en esta boca (si se reasigno/desconecto, para
    # que su boca vuelva a verse libre) y el switch destino NUEVO (si se
    # conecto a una boca especifica de otro dispositivo) -- si no, el otro
    # lado del cable se queda mostrando el estado viejo hasta recargar toda
    # la pagina.
    ids_relacionados = set()
    if puerto:
        anterior_destino_id = db.get_destino_dispositivo_anterior(dispositivo_id, puerto)
        if anterior_destino_id:
            ids_relacionados.add(anterior_destino_id)
        db.set_puerto_destino(dispositivo_id, puerto, destino_tipo, destino_id, destino_puerto)
    if destino_tipo == "dispositivo" and destino_id:
        ids_relacionados.add(destino_id)
    ids_relacionados.discard(dispositivo_id)

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        # Devuelve los puertos ya frescos de ESTE dispositivo para que el
        # frontend actualice el mapa in-place, sin recargar toda la pagina
        # (asi se puede seguir asignando bocas del mismo switch sin perder
        # el lugar -- antes cada Guardar recargaba y cerraba el acordeon).
        dispositivos = _dispositivos_con_puertos()
        por_id = {x["id"]: x for x in dispositivos}
        d = por_id.get(dispositivo_id)
        if d is None:
            return jsonify({"ok": False}), 404
        relacionados = [
            {"id": rid, "puertos": por_id[rid]["puertos"]}
            for rid in ids_relacionados if rid in por_id
        ]
        return jsonify({"ok": True, "puertos": d["puertos"], "relacionados": relacionados})

    return redirect(url_for("topologia.topologia"))
