"""Administracion -- quinto y ultimo modulo grande movido fuera de app.py
(ver blueprints/disponibilidad.py para el patron). En realidad son 4
sub-dominios que compartian el prefijo /admin en el app.py original:
inventario de equipos (import/eliminar masivo), gestion de empleados
(usuarios + import + sync con Firebase), parametros/catalogos
(departamentos/ciudades). Se dejan juntos en un solo archivo porque las
plantillas admin*.html ya comparten las mismas 4 pestanas (ver active_tab).

OJO al moverlo: los 20 endpoints pasan a llamarse "admin.<vista>" (ej.
admin.admin, admin.admin_equipos) -- se actualizaron todos los url_for()
correspondientes en las plantillas Y en las rutas de app.py que siguen
afuera del blueprint (ej. crear_equipo_manual() redirige a
"admin.admin_equipos" cuando falta la IP).
"""
import io
import json
import re
import uuid
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, redirect, render_template, request, url_for

import apagado_remoto
import db
import firebase_sync
from parsers import (
    _leer_xlsx, _parsear_empleados_html, _parsear_gestion_equipos,
    _parsear_gestion_usuarios, _parsear_inventario_html,
)

bp = Blueprint("admin", __name__)

RESULTS_DIR = Path(__file__).resolve().parent.parent.parent / "scanner" / "results"
UPLOAD_DIR = Path(__file__).resolve().parent.parent / "static" / "uploads"
ALLOWED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

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


def _row_with_ports(row):
    row = dict(row)
    row["open_ports"] = json.loads(row["open_ports"] or "[]")
    return row


@bp.route("/admin/equipos")
def admin_equipos():
    equipos = [_row_with_ports(e) for e in db.list_equipos()]
    ticket_counts = db.get_open_ticket_counts()
    for e in equipos:
        e["tickets_abiertos"] = ticket_counts.get(e["id"], 0)
        ubicacion_partes = [p for p in (e.get("sucursal"), e.get("ciudad")) if p]
        e["ubicacion"] = " / ".join(ubicacion_partes) if ubicacion_partes else None
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
        }

    # Si venimos de un choque de IP al editar (ver ficha()), esto le dice a la
    # fila de ESE equipo especifico que muestre el boton "Fusionar" y se abra
    # sola -- el resto de las filas de la tabla no se tocan.
    error_equipo_id = request.args.get("equipo_id", type=int)
    ip_conflicto = None
    if request.args.get("error") == "ip_duplicada" and request.args.get("ip_conflicto_id"):
        ip_conflicto = db.get_equipo(int(request.args["ip_conflicto_id"]))

    return render_template(
        "admin_equipos.html",
        equipos=equipos,
        scan_files=scan_files,
        usuarios=usuarios,
        usuarios_perfil=usuarios_perfil,
        dispositivos=dispositivos,
        error=request.args.get("error"),
        error_equipo_id=error_equipo_id,
        ip_conflicto=ip_conflicto,
        ip_nueva=request.args.get("ip_nueva"),
        resumen_importacion=resumen_importacion,
        resumen_apagado=resumen_apagado,
        active_tab="equipos",
        hoy=datetime.now().strftime("%Y-%m-%d"),
        categorias_equipo=db.CATEGORIAS_EQUIPO,
    )


@bp.route("/admin/equipos/eliminar_masivo", methods=["POST"])
def eliminar_equipos_masivo():
    """Borra de una sola vez los equipos que el usuario haya marcado con el
    checkbox en Inventario de Equipos (pensado para limpiar duplicados/basura
    que trajo una importacion masiva)."""
    ids = [int(i) for i in request.form.getlist("equipo_ids") if i.isdigit()]
    if ids:
        db.delete_equipos(ids)
    return redirect(url_for("admin.admin_equipos"))


@bp.route("/admin/equipos/apagar_masivo", methods=["POST"])
def apagar_equipos_masivo():
    """Apaga o reinicia de una sola vez los equipos marcados con el checkbox
    en Inventario de Equipos (ver apagado_remoto.py). Fase 1: solo alcanza
    equipos en la LAN local de Rendic/Rendic2 -- el resto vuelve marcado
    como "fuera de alcance" en el resumen, sin intentar nada con ellos.
    "Reiniciar" existe sobre todo para probar que el mecanismo (cuenta admin
    compartida + firewall) funciona sin dejar un equipo apagado de verdad si
    algo sale mal -- el equipo vuelve solo."""
    accion = request.form.get("accion") if request.form.get("accion") in ("apagar", "reiniciar") else "apagar"
    ids = [int(i) for i in request.form.getlist("equipo_ids") if i.isdigit()]
    if not ids:
        return redirect(url_for("admin.admin_equipos"))
    equipos = [db.get_equipo(i) for i in ids]
    equipos = [e for e in equipos if e]
    resumen = apagado_remoto.ejecutar_comando_equipos(equipos, accion)
    return redirect(url_for(
        "admin.admin_equipos",
        apagado="1",
        ap_accion=accion,
        ap_ok=len(resumen["ok"]),
        ap_fuera=len(resumen["fuera_de_alcance"]),
        ap_fallo=len(resumen["fallidos"]),
    ))


@bp.route("/admin/equipos/importar_inventario", methods=["POST"])
def importar_inventario():
    """Importa un inventario externo ya escrito a mano (ej. un Excel exportado
    de otro sistema, guardado como tabla HTML con extension .xls). Completa
    solo los campos vacios de los equipos que el scanner ya detecto por IP, y
    crea los que todavia no existian."""
    archivo = request.files.get("archivo")
    if not archivo or not archivo.filename:
        return redirect(url_for("admin.admin_equipos", error="archivo_requerido"))

    contenido = archivo.read().decode("utf-8", errors="replace")
    filas = _parsear_inventario_html(contenido)
    if not filas:
        return redirect(url_for("admin.admin_equipos", error="archivo_sin_filas"))

    resumen = db.importar_inventario_masivo(filas)
    return redirect(url_for(
        "admin.admin_equipos", importado="1",
        creados=resumen["creados"], actualizados=resumen["actualizados"],
        sin_cambios=resumen["sin_cambios"], omitidos=resumen["omitidos"], total=resumen["total"],
    ))


def _guardar_foto_empleado(request):
    """Procesa el campo de foto de perfil del formulario de empleado: puede
    venir como archivo subido (foto_archivo) o como URL pegada (foto_url).
    Devuelve (valor_foto_perfil, hubo_cambio). Si no vino nada nuevo,
    hubo_cambio=False para que el caller conserve la foto que ya tenia."""
    archivo = request.files.get("foto_archivo")
    if archivo and archivo.filename:
        ext = Path(archivo.filename).suffix.lower()
        if ext not in ALLOWED_IMAGE_EXT:
            return None, False
        nombre_archivo = f"empleado_{uuid.uuid4().hex}{ext}"
        archivo.save(UPLOAD_DIR / nombre_archivo)
        return f"uploads/{nombre_archivo}", True

    url = request.form.get("foto_url", "").strip()
    if url:
        return url, True

    return None, False


@bp.route("/admin")
def admin():
    usuarios = db.list_usuarios()
    equipos_count = db.get_equipos_count_por_responsable()
    for u in usuarios:
        u["equipos_count"] = equipos_count.get(u["id"], 0)
        u["equipos_asignados"] = db.list_equipos_por_responsable(u["id"])
    departamentos = db.list_departamentos()
    ciudades = db.list_ciudades()
    equipos_basico = db.list_equipos_basico()

    resumen_importacion = None
    if request.args.get("importado") == "1":
        resumen_importacion = {
            "creados": int(request.args.get("creados", 0)),
            "actualizados": int(request.args.get("actualizados", 0)),
            "sin_cambios": int(request.args.get("sin_cambios", 0)),
            "omitidos": int(request.args.get("omitidos", 0)),
            "total": int(request.args.get("total", 0)),
        }

    resumen_importacion_gestion = None
    if request.args.get("importado_gestion") == "1":
        resumen_importacion_gestion = {
            "usuarios": {
                "creados": int(request.args.get("u_creados", 0)),
                "actualizados": int(request.args.get("u_actualizados", 0)),
            },
            "equipos": {
                "creados": int(request.args.get("e_creados", 0)),
                "actualizados": int(request.args.get("e_actualizados", 0)),
            },
        }

    resumen_firebase = None
    if request.args.get("sincronizado") == "1":
        resumen_firebase = {
            "usuarios": {
                "bajados_nuevos": int(request.args.get("fu_bajados", 0)),
                "actualizados": int(request.args.get("fu_actualizados", 0)),
                "subidos": int(request.args.get("fu_subidos", 0)),
            },
            "equipos": {
                "bajados_nuevos": int(request.args.get("fe_bajados", 0)),
                "actualizados": int(request.args.get("fe_actualizados", 0)),
                "subidos": int(request.args.get("fe_subidos", 0)),
            },
        }

    return render_template(
        "admin.html", usuarios=usuarios, departamentos=departamentos, ciudades=ciudades,
        equipos_basico=equipos_basico, perfil_abierto=request.args.get("perfil", type=int),
        active_tab="empleados", resumen_importacion=resumen_importacion,
        resumen_importacion_gestion=resumen_importacion_gestion,
        resumen_firebase=resumen_firebase,
        error=request.args.get("error"),
        hoy=datetime.now().strftime("%Y-%m-%d"),
    )


@bp.route("/admin/sincronizar_firebase", methods=["POST"])
def sincronizar_firebase():
    """Arranca la sincronizacion con Firebase en segundo plano (no bloquea
    la request) para que el navegador pueda mostrar una barra de progreso
    haciendo poll a /admin/sincronizar_firebase/estado."""
    iniciado = firebase_sync.iniciar_sincronizacion_async()
    return jsonify({"iniciado": iniciado})


@bp.route("/admin/sincronizar_firebase/estado")
def sincronizar_firebase_estado():
    return jsonify(firebase_sync.obtener_estado())


@bp.route("/admin/importar_gestion", methods=["POST"])
def importar_gestion():
    """Importa el archivo .xlsx real de otro sistema de gestion (hojas
    Usuarios/Equipos/Departamentos) -- distinto al resto de los importadores,
    que leen un .xls que en realidad es una tabla HTML."""
    archivo = request.files.get("archivo")
    if not archivo or not archivo.filename:
        return redirect(url_for("admin.admin", error="archivo_requerido"))

    try:
        hojas = _leer_xlsx(io.BytesIO(archivo.read()))
    except (zipfile.BadZipFile, KeyError, ET.ParseError):
        return redirect(url_for("admin.admin", error="archivo_invalido"))

    usuarios_filas = _parsear_gestion_usuarios(hojas.get("Usuarios", []))
    equipos_filas = _parsear_gestion_equipos(hojas.get("Equipos", []))
    departamentos_hoja = hojas.get("Departamentos", [])

    if not usuarios_filas and not equipos_filas:
        return redirect(url_for("admin.admin", error="archivo_sin_filas"))

    if departamentos_hoja:
        encabezados = [(h or "").strip().lower() for h in departamentos_hoja[0]]
        if "nombre" in encabezados:
            idx_nombre = encabezados.index("nombre")
            for fila in departamentos_hoja[1:]:
                if idx_nombre < len(fila) and fila[idx_nombre]:
                    db.create_departamento(fila[idx_nombre].strip())

    resumen = db.importar_gestion_masiva(usuarios_filas, equipos_filas)
    return redirect(url_for(
        "admin.admin", importado_gestion="1",
        u_creados=resumen["usuarios"]["creados"], u_actualizados=resumen["usuarios"]["actualizados"],
        e_creados=resumen["equipos"]["creados"], e_actualizados=resumen["equipos"]["actualizados"],
    ))


@bp.route("/admin/importar_empleados", methods=["POST"])
def importar_empleados():
    """Importa el Directorio de Responsables desde un archivo externo ya
    escrito a mano (tabla HTML guardada como .xls). Matchea por nombre y solo
    completa los campos vacios -- no pisa datos ya cargados."""
    archivo = request.files.get("archivo")
    if not archivo or not archivo.filename:
        return redirect(url_for("admin.admin", error="archivo_requerido"))

    contenido = archivo.read().decode("utf-8", errors="replace")
    filas = _parsear_empleados_html(contenido)
    if not filas:
        return redirect(url_for("admin.admin", error="archivo_sin_filas"))

    resumen = db.importar_empleados_masivo(filas)
    return redirect(url_for(
        "admin.admin", importado="1",
        creados=resumen["creados"], actualizados=resumen["actualizados"],
        sin_cambios=resumen["sin_cambios"], omitidos=resumen["omitidos"], total=resumen["total"],
    ))


@bp.route("/admin/usuarios", methods=["POST"])
def crear_usuario():
    nombre = request.form.get("nombre", "").strip()
    if nombre:
        foto_perfil, _ = _guardar_foto_empleado(request)
        db.create_usuario(
            nombre,
            request.form.get("correo", "").strip() or None,
            request.form.get("cargo", "").strip() or None,
            request.form.get("sucursal", "").strip() or None,
            request.form.get("telefono", "").strip() or None,
            foto_perfil=foto_perfil,
            departamento=request.form.get("departamento", "").strip() or None,
            ciudad=request.form.get("ciudad", "").strip() or None,
            lugar_trabajo=request.form.get("lugar_trabajo", "Presencial"),
            sistemas_autorizados=request.form.get("sistemas_autorizados", "").strip() or None,
            tipo_vpn=request.form.get("tipo_vpn", "").strip() or None,
            vpn_activa=request.form.get("vpn_activa") == "on",
            activo=request.form.get("activo") == "on",
        )
    return redirect(url_for("admin.admin"))


@bp.route("/admin/usuarios/<int:usuario_id>", methods=["POST"])
def editar_usuario(usuario_id):
    foto_perfil, hubo_cambio = _guardar_foto_empleado(request)
    db.update_usuario(
        usuario_id,
        request.form.get("nombre", "").strip(),
        request.form.get("correo", "").strip() or None,
        request.form.get("cargo", "").strip() or None,
        request.form.get("sucursal", "").strip() or None,
        request.form.get("telefono", "").strip() or None,
        foto_perfil=foto_perfil,
        departamento=request.form.get("departamento", "").strip() or None,
        ciudad=request.form.get("ciudad", "").strip() or None,
        lugar_trabajo=request.form.get("lugar_trabajo", "Presencial"),
        sistemas_autorizados=request.form.get("sistemas_autorizados", "").strip() or None,
        tipo_vpn=request.form.get("tipo_vpn", "").strip() or None,
        vpn_activa=request.form.get("vpn_activa") == "on",
        activo=request.form.get("activo") == "on",
        actualizar_foto=hubo_cambio,
    )
    return redirect(url_for("admin.admin"))


@bp.route("/admin/usuarios/<int:usuario_id>/estado", methods=["POST"])
def usuario_estado(usuario_id):
    activo = request.form.get("activo") == "1"
    db.update_usuario_estado(usuario_id, activo)
    return redirect(url_for("admin.admin"))


@bp.route("/admin/usuarios/<int:usuario_id>/eliminar", methods=["POST"])
def eliminar_usuario(usuario_id):
    db.delete_usuario(usuario_id)
    return redirect(url_for("admin.admin"))


@bp.route("/admin/usuarios/eliminar_masivo", methods=["POST"])
def eliminar_usuarios_masivo():
    """Borra de una sola vez los empleados marcados con el checkbox en
    Gestion de Empleados (pensado para limpiar nombres de equipos/
    dispositivos que quedaron cargados como si fueran personas)."""
    ids = [int(i) for i in request.form.getlist("usuario_ids") if i.isdigit()]
    if ids:
        db.delete_usuarios(ids)
    return redirect(url_for("admin.admin"))


@bp.route("/admin/usuarios/<int:usuario_id>/equipos/vincular", methods=["POST"])
def vincular_equipo_usuario(usuario_id):
    equipo_id = request.form.get("equipo_id")
    if equipo_id:
        db.set_responsable_equipo(int(equipo_id), usuario_id)
    return redirect(url_for("admin.admin", perfil=usuario_id))


@bp.route("/admin/usuarios/<int:usuario_id>/equipos/<int:equipo_id>/desvincular", methods=["POST"])
def desvincular_equipo_usuario(usuario_id, equipo_id):
    db.set_responsable_equipo(equipo_id, None)
    return redirect(url_for("admin.admin", perfil=usuario_id))


@bp.route("/admin/parametros")
def admin_parametros():
    return render_template(
        "admin_parametros.html", departamentos=db.list_departamentos(), ciudades=db.list_ciudades(),
        active_tab="parametros",
    )


@bp.route("/admin/departamentos", methods=["POST"])
def crear_departamento():
    nombre = request.form.get("nombre", "").strip()
    if nombre:
        db.create_departamento(nombre)
    return redirect(url_for("admin.admin_parametros"))


@bp.route("/admin/departamentos/<int:departamento_id>/eliminar", methods=["POST"])
def eliminar_departamento(departamento_id):
    db.delete_departamento(departamento_id)
    return redirect(url_for("admin.admin_parametros"))


@bp.route("/admin/ciudades", methods=["POST"])
def crear_ciudad():
    nombre = request.form.get("nombre", "").strip()
    if nombre:
        db.create_ciudad(nombre)
    return redirect(url_for("admin.admin_parametros"))


@bp.route("/admin/ciudades/<int:ciudad_id>/eliminar", methods=["POST"])
def eliminar_ciudad(ciudad_id):
    db.delete_ciudad(ciudad_id)
    return redirect(url_for("admin.admin_parametros"))
