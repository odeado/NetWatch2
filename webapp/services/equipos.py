"""Win NetWatch RMM - Servicios de Equipos
==========================================
Logica de negocio para la ficha de un equipo, separada de las rutas de
Flask (app.py) -- Fase 2 del reorden. La idea es que la ruta quede
delgada: recibe el request, llama a una funcion de aca, y decide que
responder (redirect/render/fragmento ajax) segun el resultado. El acceso
a sqlite sigue viviendo solo en db/ (ver webapp/db/__init__.py); este
modulo no abre conexiones, solo llama a las funciones de db y valida/
arma los datos antes y despues.
"""
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import json
import re
import uuid

import db
import firebase_sync

BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "static" / "uploads"
ALLOWED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


@dataclass
class ResultadoActualizarFicha:
    """Lo que la ruta ficha() en app.py necesita para decidir que responder
    tras guardar el formulario. ip_error es la unica validacion que puede
    fallar (ver actualizar_ficha)."""
    ip_error: str = None
    ip_conflicto_id: int = None
    ip_nueva: str = None


def guardar_foto_equipo(request):
    """Igual que guardar_foto_empleado (blueprints/admin.py) pero para la
    foto del equipo (PC): puede venir como archivo subido (foto_archivo) o
    URL pegada (foto_url). Devuelve (valor_foto, hubo_cambio)."""
    archivo = request.files.get("foto_archivo")
    if archivo and archivo.filename:
        ext = Path(archivo.filename).suffix.lower()
        if ext not in ALLOWED_IMAGE_EXT:
            return None, False
        nombre_archivo = f"equipo_{uuid.uuid4().hex}{ext}"
        archivo.save(UPLOAD_DIR / nombre_archivo)
        return f"uploads/{nombre_archivo}", True

    url = request.form.get("foto_url", "").strip()
    if url:
        return url, True

    return None, False


def actualizar_ficha(equipo_id, request):
    """Aplica los cambios del formulario de ficha (webapp/templates/ficha.html
    y el panel lateral/acordeon que comparten el mismo formulario) a un
    equipo. Devuelve None si el equipo no existe (la ruta decide que hacer
    con eso), o un ResultadoActualizarFicha con el resultado de la
    validacion de IP."""
    equipo_actual = db.get_equipo(equipo_id)
    if not equipo_actual:
        return None

    fields = {k: (request.form.get(k, "").strip() or None) for k in db.FICHA_FIELDS}
    fields["critico"] = 1 if request.form.get("critico") == "on" else 0
    fields["gestionado"] = 1 if request.form.get("gestionado") == "on" else 0
    fields["ip_temporal"] = 1 if request.form.get("ip_temporal") == "on" else 0

    if request.form.get("eliminar_foto") == "1":
        fields["foto"] = None
    else:
        foto, hubo_cambio_foto = guardar_foto_equipo(request)
        if hubo_cambio_foto:
            fields["foto"] = foto

    responsable_id_raw = request.form.get("responsable_id") or None
    responsable_id = int(responsable_id_raw) if responsable_id_raw else None
    if responsable_id:
        usuario = db.get_usuario(responsable_id)
        fields["responsable_id"] = responsable_id
        fields["responsable"] = usuario["nombre"] if usuario else None
        fields["correo_responsable"] = usuario["correo"] if usuario else None
    else:
        fields["responsable_id"] = None
        fields["responsable"] = None
        fields["correo_responsable"] = None

    dispositivo_id = request.form.get("dispositivo_id") or None
    fields["dispositivo_id"] = int(dispositivo_id) if dispositivo_id else None

    # La IP es la clave unica que usa el escaner para hacer match con cada
    # fila -- validamos que no quede vacia ni choque con otro equipo antes
    # de guardar (equipos.ip es NOT NULL UNIQUE en la base de datos).
    ip_error = None
    ip_conflicto_id = None
    nueva_ip = request.form.get("ip", "").strip()
    if not nueva_ip:
        ip_error = "ip_requerida"
    elif nueva_ip != equipo_actual["ip"]:
        existente = db.get_equipo_by_ip(nueva_ip)
        if existente and existente != equipo_id:
            # Caso comun: un equipo cambio de IP de verdad (la vieja se
            # dio de baja) pero el escaneo ya creo un registro aparte para
            # la IP nueva apenas la vio en la red. Se ofrece "Fusionar" en
            # vez de solo bloquear el cambio (ver fusionar_equipo_ip en
            # app.py y db.fusionar_equipo_por_ip).
            ip_error = "ip_duplicada"
            ip_conflicto_id = existente
        else:
            fields["ip"] = nueva_ip

    db.update_ficha(equipo_id, fields)

    return ResultadoActualizarFicha(
        ip_error=ip_error, ip_conflicto_id=ip_conflicto_id,
        ip_nueva=nueva_ip if ip_error else None,
    )


# --- vista de Inventario de Equipos (Administracion) ---------------------
RESULTS_DIR = Path(__file__).resolve().parent.parent.parent / "scanner" / "results"
_IP_ORDEN_RE = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")


def _ip_orden_key(equipo):
    """Ordena por IP real de menor a mayor (172.30.100.9 antes que
    172.30.100.80); los equipos sin IP real (manuales/importados sin IP fija,
    que usan el hostname como identificador) quedan al final, ordenados por
    hostname para que la lista no quede desordenada."""
    m = _IP_ORDEN_RE.match(equipo.get("ip") or "")
    if m:
        return (0, tuple(int(g) for g in m.groups()))
    return (1, (equipo.get("hostname") or equipo.get("ip") or "").lower())


def _row_with_ports_admin(row):
    row = dict(row)
    row["open_ports"] = json.loads(row["open_ports"] or "[]")
    return row


def armar_vista_admin_equipos(request):
    """Arma todos los datos que blueprints/admin.py:admin_equipos() necesita
    para renderizar Inventario de Equipos: la lista de equipos con sus
    campos calculados (tickets abiertos, ubicacion, agente de apagado), los
    catalogos para los selects/modales, y los resumenes de import/apagado
    que llegan por query string tras un redirect. Devuelve un dict listo
    para pasarle a render_template como **contexto -- no escribe nada en la
    base, solo lee y arma la vista."""
    equipos = [_row_with_ports_admin(e) for e in db.list_equipos()]
    ticket_counts = db.get_open_ticket_counts()
    # Tickets de soporte inline en el acordeon de cada equipo (Andres lo pidio
    # 2026-08-27, para no tener que abrir Ficha completa solo para crear un
    # ticket rapido). Una sola consulta para TODOS los tickets y se agrupan
    # aca en memoria -- evita N consultas (una por equipo) en un inventario
    # que puede tener cientos de filas; la cantidad total de tickets del
    # sistema es chica en comparacion, asi que esto sale barato.
    tickets_por_equipo = {}
    for t in db.list_all_tickets():
        tickets_por_equipo.setdefault(t["equipo_id"], []).append(t)
    # Agente de apagado/reinicio remoto (ver agente_apagado/): cada PC con el
    # agente instalado deja su propio heartbeat en Firebase por IP (ver
    # firebase_sync.leer_agentes_estado). Se matchea directo por IP del
    # equipo -- esto va por maquina, no por empleado (antes se puso por
    # error en Gestion de Empleados).
    agentes_estado = firebase_sync.leer_agentes_estado()
    for e in equipos:
        e["tickets_abiertos"] = ticket_counts.get(e["id"], 0)
        ubicacion_partes = [p for p in (e.get("sucursal"), e.get("ciudad")) if p]
        e["ubicacion"] = " / ".join(ubicacion_partes) if ubicacion_partes else None
        info = agentes_estado.get(e.get("ip"))
        e["agente_visto_en"] = info.get("visto_en") if info else None
    equipos.sort(key=_ip_orden_key)

    scan_files = [f.name for f in db.list_scan_files(RESULTS_DIR)]
    usuarios = db.list_usuarios(solo_activos=True)
    dispositivos = db.list_dispositivos()

    # Para el modal "ver ficha del responsable" inline (sin salir de Inventario
    # de Equipos): trae TODOS los usuarios (no solo activos, para no fallar si
    # el responsable de un equipo quedo inactivo) con sus equipos asignados.
    usuarios_perfil = db.list_usuarios()
    for u in usuarios_perfil:
        u["equipos_asignados"] = db.list_equipos_por_responsable(u["id"])

    resumen_importacion = None
    if request.args.get("importado") == "1":
        resumen_importacion = {
            "creados": int(request.args.get("creados", 0)),
            "actualizados": int(request.args.get("actualizados", 0)),
            "sin_cambios": int(request.args.get("sin_cambios", 0)),
            "omitidos": int(request.args.get("omitidos", 0)),
            "total": int(request.args.get("total", 0)),
        }

    resumen_apagado = None
    if request.args.get("apagado") == "1":
        resumen_apagado = {
            "accion": request.args.get("ap_accion", "apagar"),
            "ok": int(request.args.get("ap_ok", 0)),
            "fuera": int(request.args.get("ap_fuera", 0)),
            "fallo": int(request.args.get("ap_fallo", 0)),
            "detalle": request.args.get("ap_detalle") or None,
            # Confirmacion real de ejecucion (no solo "quedo encolado"): ver
            # apagado_remoto.iniciar_encolado_async, fase "confirmando".
            "confirmados": int(request.args.get("ap_confirmados", 0)),
            "sin_confirmar": int(request.args.get("ap_sin_confirmar", 0)),
            # Solo viene poblado para "reiniciar" (fase "esperando_vuelta"):
            # cuantos ya avisaron de vuelta despues de bootear.
            "vueltos": int(request.args.get("ap_vueltos", 0)),
            "sin_volver": int(request.args.get("ap_sin_volver", 0)),
        }

    # Si venimos de un choque de IP al editar (ver ficha() en app.py), esto le
    # dice a la fila de ESE equipo especifico que muestre el boton "Fusionar"
    # y se abra sola -- el resto de las filas de la tabla no se tocan.
    error_equipo_id = request.args.get("equipo_id", type=int)
    ip_conflicto = None
    if request.args.get("error") == "ip_duplicada" and request.args.get("ip_conflicto_id"):
        ip_conflicto = db.get_equipo(int(request.args["ip_conflicto_id"]))

    return dict(
        equipos=equipos,
        scan_files=scan_files,
        usuarios=usuarios,
        usuarios_perfil=usuarios_perfil,
        dispositivos=dispositivos,
        error=request.args.get("error"),
        error_equipo_id=error_equipo_id,
        ip_conflicto=ip_conflicto,
        ip_nueva=request.args.get("ip_nueva"),
        tickets_por_equipo=tickets_por_equipo,
        resumen_importacion=resumen_importacion,
        resumen_apagado=resumen_apagado,
        active_tab="equipos",
        hoy=datetime.now().strftime("%Y-%m-%d"),
        categorias_equipo=db.CATEGORIAS_EQUIPO,
    )
