#!/usr/bin/env python3
"""
Win NetWatch RMM - Interfaz Web + Inventario + Tickets + RDP (v6)
====================================================================
Muestra el inventario, el estado en linea/fuera de linea de cada equipo,
tickets de soporte por equipo, un panel global de tickets para triage
diario, y acceso RDP en un clic:

- /equipo/<id>/rdp-open : ruta principal, registra el historial y muestra
  una pagina que redirige al protocolo netwatchrdp:// para abrir el
  Escritorio Remoto nativo de Windows directo (requiere instalar una vez
  tools/instalar_protocolo_rdp.bat).
- /equipo/<id>/rdp : respaldo, descarga un archivo .rdp normal por si el
  protocolo no esta instalado todavia.

Requiere Flask (pip install -r requirements.txt). Corre con: python app.py
Luego abre http://localhost:5001 - la pagina se refresca sola cada 20s.
"""

import io
import json
import re
import time
import uuid
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, jsonify, make_response, redirect, render_template, request, url_for

import db
import firebase_sync
from parsers import (
    _leer_xlsx, _parsear_empleados_html, _parsear_gestion_equipos,
    _parsear_gestion_usuarios, _parsear_inventario_html,
)
from blueprints.disponibilidad import bp as disponibilidad_bp
from blueprints.export import bp as export_bp
from blueprints.tickets import bp as tickets_bp
from blueprints.topologia import bp as topologia_bp

BASE_DIR = Path(__file__).resolve().parent
RESULTS_DIR = BASE_DIR.parent / "scanner" / "results"
LEGACY_CONFIRM_FILE = BASE_DIR / "confirmations.json"
UPLOAD_DIR = BASE_DIR / "static" / "uploads"
MONITOR_LOG_FILE = BASE_DIR / "monitor.log"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
ALLOWED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

app = Flask(__name__)
db.init_db()
app.register_blueprint(disponibilidad_bp)
app.register_blueprint(export_bp)
app.register_blueprint(tickets_bp)
app.register_blueprint(topologia_bp)


def _whatsapp_link(telefono):
    """Normaliza un numero chileno (con o sin +56, con o sin espacios/guiones)
    a un link https://wa.me/... que abre WhatsApp directo con ese contacto."""
    if not telefono:
        return None
    digitos = "".join(c for c in telefono if c.isdigit())
    if not digitos:
        return None
    if digitos.startswith("56") and len(digitos) in (10, 11):
        pass
    elif len(digitos) == 9 and digitos.startswith("9"):
        digitos = "56" + digitos
    elif len(digitos) == 8:
        digitos = "569" + digitos
    # whatsapp://send abre la app directo (protocolo nativo), a diferencia de
    # wa.me/api.whatsapp.com que primero pasa por una pagina intermedia.
    return f"whatsapp://send?phone={digitos}"


app.jinja_env.filters["whatsapp_link"] = _whatsapp_link


def _tiempo_relativo(iso):
    """Misma fraseo que netwatchTiempoRelativo() en index.html (JS), pero en
    Python -- para paginas como /disponibilidad que se renderizan del lado
    del servidor y no tienen el polling en vivo de la pagina principal."""
    if not iso:
        return None
    try:
        entonces = datetime.fromisoformat(iso)
    except ValueError:
        return None
    segundos = max(0, (datetime.now() - entonces).total_seconds())
    if segundos < 60:
        return "hace segundos"
    minutos = round(segundos / 60)
    if minutos < 60:
        return f"hace {minutos} min"
    horas = round(minutos / 60)
    if horas < 24:
        return f"hace {horas} hora" + ("" if horas == 1 else "s")
    dias = round(horas / 24)
    return f"hace {dias} dia" + ("" if dias == 1 else "s")


app.jinja_env.filters["tiempo_relativo"] = _tiempo_relativo


def _row_with_ports(row):
    row = dict(row)
    row["open_ports"] = json.loads(row["open_ports"] or "[]")
    return row


def _build_estado_payload(estado_filtro=None, eventos_limit=30):
    """Arma el paquete de datos (equipos + resumen + eventos recientes) que
    usan tanto la carga inicial de / como el polling en vivo de /api/estado,
    para que ambos queden siempre en sincronia con una sola fuente de verdad."""
    equipos = [_row_with_ports(e) for e in db.list_equipos(estado_filtro)]
    ticket_counts = db.get_open_ticket_counts()
    dispositivos_por_id = {d["id"]: d["nombre"] for d in db.list_dispositivos()}
    for e in equipos:
        e["tickets_abiertos"] = ticket_counts.get(e["id"], 0)
        e["has_rdp"] = any(p.get("port") == 3389 for p in e["open_ports"])
        ubicacion_partes = [p for p in (e.get("sucursal"), e.get("ciudad")) if p]
        e["ubicacion"] = " / ".join(ubicacion_partes) if ubicacion_partes else None
        e["dispositivo_nombre"] = dispositivos_por_id.get(e.get("dispositivo_id"))

    todos = db.list_equipos()
    summary = {
        "total": len(todos),
        "confirmados": sum(1 for r in todos if r["estado_deteccion"] == "confirmado"),
        "descartados": sum(1 for r in todos if r["estado_deteccion"] == "descartado"),
        "pendientes": sum(1 for r in todos if r["estado_deteccion"] == "pendiente"),
        "en_linea": sum(1 for r in todos if r["en_linea"]),
        "fuera_de_linea": sum(1 for r in todos if not r["en_linea"]),
        "tickets_abiertos": db.count_open_tickets(),
    }
    eventos = db.list_recent_events(eventos_limit)
    return equipos, summary, eventos


@app.route("/")
def index():
    equipos, summary, eventos = _build_estado_payload()
    return render_template(
        "index.html",
        rows=equipos,
        summary=summary,
        eventos=eventos,
    )


@app.route("/api/estado")
def api_estado():
    """JSON liviano para el polling en vivo del inventario: fichas + resumen +
    eventos recientes (online/offline/nuevo), usado para refrescar la grilla
    sin recargar la pagina y para disparar los toasts de aviso."""
    equipos, summary, eventos = _build_estado_payload()
    return jsonify({"equipos": equipos, "summary": summary, "eventos": eventos})


@app.route("/api/devices/agent-report", methods=["POST"])
def agent_report():
    """Recibe el auto-reporte que manda tools/agente_inventario.ps1 desde cada
    PC (WMI local -- CPU/RAM/marca/modelo/serie/OS/Office/Antivirus reales,
    mas confiables que lo que el escaneo de red puede adivinar). Hace upsert
    por IP, igual que el escaneo, pero nunca toca campos administrativos
    (responsable, sucursal, categoria, notas, critico) que el tecnico haya
    cargado a mano en la ficha."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "cuerpo JSON invalido"}), 400
    ip = (data.get("ip") or "").strip()
    if not ip:
        return jsonify({"ok": False, "error": "falta 'ip'"}), 400
    equipo_id, es_nuevo = db.aplicar_reporte_agente(data)
    return jsonify({"ok": True, "equipo_id": equipo_id, "nuevo": es_nuevo})


@app.route("/api/monitor_log")
def api_monitor_log():
    """Ultimas lineas de monitor.py, para mostrar su consola embebida en la
    pagina en vez de tener que dejar abierta la ventana negra aparte.
    'activo' es una estimacion: si el log no se actualizo hace rato, lo mas
    probable es que monitor.py no este corriendo (o se haya caido)."""
    if not MONITOR_LOG_FILE.exists():
        return jsonify({"lineas": [], "activo": False, "existe": False})

    try:
        with open(MONITOR_LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            lineas = f.readlines()
    except OSError:
        lineas = []

    ultimas = [l.rstrip("\n") for l in lineas[-200:]]
    segundos_desde_ultima_escritura = time.time() - MONITOR_LOG_FILE.stat().st_mtime
    activo = segundos_desde_ultima_escritura < 180  # generoso: cubre intervalos tipicos de 30-120s
    return jsonify({"lineas": ultimas, "activo": activo, "existe": True})


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


@app.route("/admin/equipos")
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
        active_tab="equipos",
        hoy=datetime.now().strftime("%Y-%m-%d"),
        categorias_equipo=db.CATEGORIAS_EQUIPO,
    )


@app.route("/admin/equipos/eliminar_masivo", methods=["POST"])
def eliminar_equipos_masivo():
    """Borra de una sola vez los equipos que el usuario haya marcado con el
    checkbox en Inventario de Equipos (pensado para limpiar duplicados/basura
    que trajo una importacion masiva)."""
    ids = [int(i) for i in request.form.getlist("equipo_ids") if i.isdigit()]
    if ids:
        db.delete_equipos(ids)
    return redirect(url_for("admin_equipos"))


@app.route("/admin/equipos/importar_inventario", methods=["POST"])
def importar_inventario():
    """Importa un inventario externo ya escrito a mano (ej. un Excel exportado
    de otro sistema, guardado como tabla HTML con extension .xls). Completa
    solo los campos vacios de los equipos que el scanner ya detecto por IP, y
    crea los que todavia no existian."""
    archivo = request.files.get("archivo")
    if not archivo or not archivo.filename:
        return redirect(url_for("admin_equipos", error="archivo_requerido"))

    contenido = archivo.read().decode("utf-8", errors="replace")
    filas = _parsear_inventario_html(contenido)
    if not filas:
        return redirect(url_for("admin_equipos", error="archivo_sin_filas"))

    resumen = db.importar_inventario_masivo(filas)
    return redirect(url_for(
        "admin_equipos", importado="1",
        creados=resumen["creados"], actualizados=resumen["actualizados"],
        sin_cambios=resumen["sin_cambios"], omitidos=resumen["omitidos"], total=resumen["total"],
    ))


@app.route("/equipos/nuevo", methods=["POST"])
def crear_equipo_manual():
    ip = request.form.get("ip", "").strip()
    if not ip:
        return redirect(url_for("admin_equipos", error="ip_requerida"))
    if db.get_equipo_by_ip(ip):
        return redirect(url_for("admin_equipos", error="ip_duplicada"))

    responsable_id = request.form.get("responsable_id") or None
    equipo_id = db.create_equipo_manual(
        ip,
        hostname=request.form.get("hostname", "").strip() or None,
        mac=request.form.get("mac", "").strip() or None,
        marca=request.form.get("marca", "").strip() or None,
        modelo=request.form.get("modelo", "").strip() or None,
        responsable_id=int(responsable_id) if responsable_id else None,
        sucursal=request.form.get("sucursal", "").strip() or None,
        notas=request.form.get("notas", "").strip() or None,
    )
    return redirect(url_for("ficha", equipo_id=equipo_id))


@app.route("/import", methods=["POST"])
def import_scan():
    filename = request.form.get("file")
    files = db.list_scan_files(RESULTS_DIR)

    target = None
    if filename:
        candidate = RESULTS_DIR / filename
        if candidate.exists():
            target = candidate
    if target is None and files:
        target = files[0]

    if target:
        db.import_scan(target)
        if LEGACY_CONFIRM_FILE.exists():
            db.migrate_legacy_confirmations(LEGACY_CONFIRM_FILE)

    return redirect(url_for("admin_equipos"))


@app.route("/import-todos", methods=["POST"])
def import_scan_todos():
    # Para cuando cada sucursal remota (Matta/Arica/Iquique) deja su propio
    # archivo de escaneo en la carpeta sincronizada (OneDrive/etc.) -- en vez
    # de importar uno por uno, este boton los aplica todos de una pasada.
    for target in db.list_scan_files(RESULTS_DIR):
        db.import_scan(target)
    if LEGACY_CONFIRM_FILE.exists():
        db.migrate_legacy_confirmations(LEGACY_CONFIRM_FILE)
    return redirect(url_for("admin_equipos"))


@app.route("/confirm", methods=["POST"])
def confirm():
    equipo_id = request.form["id"]
    action = request.form["action"]
    db.update_estado(equipo_id, action)
    # Las fichas de / lo llaman por fetch (AJAX) para no perder la posicion
    # de scroll con un POST+redirect normal -- en ese caso alcanza con un OK.
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": True})
    if request.form.get("origen") == "ficha":
        return redirect(url_for("ficha", equipo_id=equipo_id))
    return redirect(url_for("index"))


@app.route("/toggle-critico", methods=["POST"])
def toggle_critico():
    equipo_id = request.form["id"]
    nuevo = 1 if request.form.get("critico") == "1" else 0
    if not db.set_critico(equipo_id, nuevo):
        return jsonify({"ok": False}), 404
    # Igual que /confirm: las fichas de / lo llaman por fetch (AJAX) para no
    # perder la posicion de scroll.
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": True, "critico": nuevo})
    if request.form.get("origen") == "ficha":
        return redirect(url_for("ficha", equipo_id=equipo_id))
    return redirect(url_for("index"))


def _guardar_foto_equipo(request):
    """Igual que _guardar_foto_empleado pero para la foto del equipo (PC):
    puede venir como archivo subido (foto_archivo) o URL pegada (foto_url).
    Devuelve (valor_foto, hubo_cambio)."""
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


def _render_panel_editar_equipo(equipo_id, error=None, ip_conflicto_id=None, ip_nueva=None):
    """Fragmento HTML (sin layout de pagina) con el formulario de edicion de
    un equipo -- usado por el panel lateral de la pantalla frontal (index),
    que edita sin navegar a la ficha completa para no perder el scroll/filtro
    de donde estaba el usuario."""
    equipo = db.get_equipo(equipo_id)
    if not equipo:
        return None
    equipo["open_ports"] = json.loads(equipo["open_ports"] or "[]")
    usuarios = db.list_usuarios(solo_activos=True)
    dispositivos = db.list_dispositivos()
    ip_conflicto = db.get_equipo(ip_conflicto_id) if ip_conflicto_id else None
    return render_template(
        "_panel_editar_equipo.html", e=equipo, usuarios=usuarios, dispositivos=dispositivos,
        categorias_equipo=db.CATEGORIAS_EQUIPO, error=error,
        ip_conflicto=ip_conflicto, ip_nueva=ip_nueva,
    )


@app.route("/equipo/<int:equipo_id>/panel")
def panel_editar_equipo(equipo_id):
    ip_conflicto_id = request.args.get("ip_conflicto_id")
    html = _render_panel_editar_equipo(
        equipo_id,
        error=request.args.get("error"),
        ip_conflicto_id=int(ip_conflicto_id) if ip_conflicto_id else None,
        ip_nueva=request.args.get("ip_nueva"),
    )
    if html is None:
        return "", 404
    return html


@app.route("/equipo/<int:equipo_id>", methods=["GET", "POST"])
def ficha(equipo_id):
    es_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    if request.method == "POST":
        equipo_actual = db.get_equipo(equipo_id)
        if not equipo_actual:
            return redirect(url_for("index"))

        fields = {k: (request.form.get(k, "").strip() or None) for k in db.FICHA_FIELDS}
        fields["critico"] = 1 if request.form.get("critico") == "on" else 0
        fields["gestionado"] = 1 if request.form.get("gestionado") == "on" else 0
        fields["ip_temporal"] = 1 if request.form.get("ip_temporal") == "on" else 0

        if request.form.get("eliminar_foto") == "1":
            fields["foto"] = None
        else:
            foto, hubo_cambio_foto = _guardar_foto_equipo(request)
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
                # vez de solo bloquear el cambio (ver fusionar_equipo_ip mas
                # abajo y db.fusionar_equipo_por_ip).
                ip_error = "ip_duplicada"
                ip_conflicto_id = existente
            else:
                fields["ip"] = nueva_ip

        db.update_ficha(equipo_id, fields)

        # Si vino del panel lateral de la pantalla frontal (fetch con
        # X-Requested-With), no se redirige a ninguna pagina -- se devuelve
        # el mismo fragmento del formulario (con el error si lo hubo) y un
        # header propio para que el JS sepa si cerrar el panel o no, asi
        # nunca se navega ni se pierde el scroll/filtro de donde se estaba.
        if es_ajax:
            html = _render_panel_editar_equipo(
                equipo_id, error=ip_error, ip_conflicto_id=ip_conflicto_id,
                ip_nueva=nueva_ip if ip_error else None,
            )
            resp = make_response(html)
            if not ip_error:
                resp.headers["X-Netwatch-Guardado"] = "1"
            return resp

        # "origen" dice desde donde se edito -- si vino del panel acordeon
        # inline de Gestion de Equipos, vuelve ahi en vez de mandar a la ficha
        # completa (asi no se sale de Inventario de Equipos y puede seguir
        # editando el siguiente equipo de la lista sin perder el lugar).
        if request.form.get("origen") == "inventario":
            if ip_error:
                return redirect(url_for(
                    "admin_equipos", error=ip_error, equipo_id=equipo_id,
                    ip_nueva=nueva_ip, ip_conflicto_id=ip_conflicto_id,
                ))
            return redirect(url_for("admin_equipos"))
        if ip_error:
            return redirect(url_for(
                "ficha", equipo_id=equipo_id, error=ip_error,
                ip_nueva=nueva_ip, ip_conflicto_id=ip_conflicto_id,
            ))
        return redirect(url_for("ficha", equipo_id=equipo_id))

    equipo = db.get_equipo(equipo_id)
    if not equipo:
        return redirect(url_for("index"))
    equipo["open_ports"] = json.loads(equipo["open_ports"] or "[]")
    tickets = db.list_tickets_for_equipo(equipo_id)
    rdp_history = db.list_rdp_history_for_equipo(equipo_id)
    usuarios = db.list_usuarios(solo_activos=True)
    dispositivos = db.list_dispositivos()
    disponibilidad = db.calcular_disponibilidad(equipo_id, dias=30) if equipo.get("origen") != "manual" else None

    ip_conflicto = None
    if request.args.get("error") == "ip_duplicada" and request.args.get("ip_conflicto_id"):
        ip_conflicto = db.get_equipo(int(request.args["ip_conflicto_id"]))

    return render_template(
        "ficha.html", e=equipo, tickets=tickets, rdp_history=rdp_history, usuarios=usuarios,
        dispositivos=dispositivos, disponibilidad=disponibilidad, error=request.args.get("error"),
        categorias_equipo=db.CATEGORIAS_EQUIPO,
        origen=request.args.get("origen"),
        ip_conflicto=ip_conflicto, ip_nueva=request.args.get("ip_nueva"),
    )


@app.route("/equipo/<int:equipo_id>/fusionar_ip", methods=["POST"])
def fusionar_equipo_ip(equipo_id):
    """Confirma la fusion ofrecida cuando el cambio de IP de un equipo choca
    con un registro duplicado (ver ficha() mas arriba y
    db.fusionar_equipo_por_ip). El duplicado se borra y esta ficha se queda
    con la IP nueva y con cualquier dato tecnico que le faltara."""
    ip_nueva = request.form.get("ip_nueva", "").strip()
    if ip_nueva:
        db.fusionar_equipo_por_ip(equipo_id, ip_nueva)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        html = _render_panel_editar_equipo(equipo_id)
        resp = make_response(html if html is not None else "")
        resp.headers["X-Netwatch-Guardado"] = "1"
        return resp
    if request.form.get("origen") == "inventario":
        return redirect(url_for("admin_equipos"))
    return redirect(url_for("ficha", equipo_id=equipo_id))


@app.route("/equipo/<int:equipo_id>/rdp")
def download_rdp(equipo_id):
    equipo = db.get_equipo(equipo_id)
    if not equipo:
        return redirect(url_for("index"))

    db.log_rdp_connection(equipo_id, equipo["ip"], equipo["hostname"], request.remote_addr)

    rdp_content = (
        f"full address:s:{equipo['ip']}\n"
        "prompt for credentials:i:1\n"
        "screen mode id:i:2\n"
        "use multimon:i:0\n"
        "desktopwidth:i:1920\n"
        "desktopheight:i:1080\n"
        "session bpp:i:32\n"
        "compression:i:1\n"
        "audiomode:i:0\n"
        "authentication level:i:2\n"
        "networkautodetect:i:1\n"
        "bandwidthautodetect:i:1\n"
    )
    safe_name = (equipo["hostname"] or equipo["ip"]).replace(" ", "_").replace(":", "-")
    return Response(
        rdp_content,
        mimetype="application/x-rdp",
        headers={"Content-Disposition": f"attachment; filename={safe_name}.rdp"},
    )


@app.route("/equipo/<int:equipo_id>/rdp-open")
def open_rdp(equipo_id):
    equipo = db.get_equipo(equipo_id)
    if not equipo:
        return redirect(url_for("index"))
    db.log_rdp_connection(equipo_id, equipo["ip"], equipo["hostname"], request.remote_addr)
    return render_template("rdp_redirect.html", equipo=equipo)


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


@app.route("/admin")
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


@app.route("/admin/sincronizar_firebase", methods=["POST"])
def sincronizar_firebase():
    """Arranca la sincronizacion con Firebase en segundo plano (no bloquea
    la request) para que el navegador pueda mostrar una barra de progreso
    haciendo poll a /admin/sincronizar_firebase/estado."""
    iniciado = firebase_sync.iniciar_sincronizacion_async()
    return jsonify({"iniciado": iniciado})


@app.route("/admin/sincronizar_firebase/estado")
def sincronizar_firebase_estado():
    return jsonify(firebase_sync.obtener_estado())


@app.route("/admin/importar_gestion", methods=["POST"])
def importar_gestion():
    """Importa el archivo .xlsx real de otro sistema de gestion (hojas
    Usuarios/Equipos/Departamentos) -- distinto al resto de los importadores,
    que leen un .xls que en realidad es una tabla HTML."""
    archivo = request.files.get("archivo")
    if not archivo or not archivo.filename:
        return redirect(url_for("admin", error="archivo_requerido"))

    try:
        hojas = _leer_xlsx(io.BytesIO(archivo.read()))
    except (zipfile.BadZipFile, KeyError, ET.ParseError):
        return redirect(url_for("admin", error="archivo_invalido"))

    usuarios_filas = _parsear_gestion_usuarios(hojas.get("Usuarios", []))
    equipos_filas = _parsear_gestion_equipos(hojas.get("Equipos", []))
    departamentos_hoja = hojas.get("Departamentos", [])

    if not usuarios_filas and not equipos_filas:
        return redirect(url_for("admin", error="archivo_sin_filas"))

    if departamentos_hoja:
        encabezados = [(h or "").strip().lower() for h in departamentos_hoja[0]]
        if "nombre" in encabezados:
            idx_nombre = encabezados.index("nombre")
            for fila in departamentos_hoja[1:]:
                if idx_nombre < len(fila) and fila[idx_nombre]:
                    db.create_departamento(fila[idx_nombre].strip())

    resumen = db.importar_gestion_masiva(usuarios_filas, equipos_filas)
    return redirect(url_for(
        "admin", importado_gestion="1",
        u_creados=resumen["usuarios"]["creados"], u_actualizados=resumen["usuarios"]["actualizados"],
        e_creados=resumen["equipos"]["creados"], e_actualizados=resumen["equipos"]["actualizados"],
    ))


@app.route("/admin/importar_empleados", methods=["POST"])
def importar_empleados():
    """Importa el Directorio de Responsables desde un archivo externo ya
    escrito a mano (tabla HTML guardada como .xls). Matchea por nombre y solo
    completa los campos vacios -- no pisa datos ya cargados."""
    archivo = request.files.get("archivo")
    if not archivo or not archivo.filename:
        return redirect(url_for("admin", error="archivo_requerido"))

    contenido = archivo.read().decode("utf-8", errors="replace")
    filas = _parsear_empleados_html(contenido)
    if not filas:
        return redirect(url_for("admin", error="archivo_sin_filas"))

    resumen = db.importar_empleados_masivo(filas)
    return redirect(url_for(
        "admin", importado="1",
        creados=resumen["creados"], actualizados=resumen["actualizados"],
        sin_cambios=resumen["sin_cambios"], omitidos=resumen["omitidos"], total=resumen["total"],
    ))


@app.route("/admin/usuarios", methods=["POST"])
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
    return redirect(url_for("admin"))


@app.route("/admin/usuarios/<int:usuario_id>", methods=["POST"])
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
    return redirect(url_for("admin"))


@app.route("/admin/usuarios/<int:usuario_id>/estado", methods=["POST"])
def usuario_estado(usuario_id):
    activo = request.form.get("activo") == "1"
    db.update_usuario_estado(usuario_id, activo)
    return redirect(url_for("admin"))


@app.route("/admin/usuarios/<int:usuario_id>/eliminar", methods=["POST"])
def eliminar_usuario(usuario_id):
    db.delete_usuario(usuario_id)
    return redirect(url_for("admin"))


@app.route("/admin/usuarios/eliminar_masivo", methods=["POST"])
def eliminar_usuarios_masivo():
    """Borra de una sola vez los empleados marcados con el checkbox en
    Gestion de Empleados (pensado para limpiar nombres de equipos/
    dispositivos que quedaron cargados como si fueran personas)."""
    ids = [int(i) for i in request.form.getlist("usuario_ids") if i.isdigit()]
    if ids:
        db.delete_usuarios(ids)
    return redirect(url_for("admin"))


@app.route("/admin/usuarios/<int:usuario_id>/equipos/vincular", methods=["POST"])
def vincular_equipo_usuario(usuario_id):
    equipo_id = request.form.get("equipo_id")
    if equipo_id:
        db.set_responsable_equipo(int(equipo_id), usuario_id)
    return redirect(url_for("admin", perfil=usuario_id))


@app.route("/admin/usuarios/<int:usuario_id>/equipos/<int:equipo_id>/desvincular", methods=["POST"])
def desvincular_equipo_usuario(usuario_id, equipo_id):
    db.set_responsable_equipo(equipo_id, None)
    return redirect(url_for("admin", perfil=usuario_id))


@app.route("/admin/parametros")
def admin_parametros():
    return render_template(
        "admin_parametros.html", departamentos=db.list_departamentos(), ciudades=db.list_ciudades(),
        active_tab="parametros",
    )


@app.route("/admin/departamentos", methods=["POST"])
def crear_departamento():
    nombre = request.form.get("nombre", "").strip()
    if nombre:
        db.create_departamento(nombre)
    return redirect(url_for("admin_parametros"))


@app.route("/admin/departamentos/<int:departamento_id>/eliminar", methods=["POST"])
def eliminar_departamento(departamento_id):
    db.delete_departamento(departamento_id)
    return redirect(url_for("admin_parametros"))


@app.route("/admin/ciudades", methods=["POST"])
def crear_ciudad():
    nombre = request.form.get("nombre", "").strip()
    if nombre:
        db.create_ciudad(nombre)
    return redirect(url_for("admin_parametros"))


@app.route("/admin/ciudades/<int:ciudad_id>/eliminar", methods=["POST"])
def eliminar_ciudad(ciudad_id):
    db.delete_ciudad(ciudad_id)
    return redirect(url_for("admin_parametros"))



if __name__ == "__main__":
    # use_reloader=False es a proposito: con el reloader activo, Flask lanza
    # DOS procesos python.exe (uno vigilante + uno de trabajo). Si "Detener
    # NetWatch.bat" alcanza a matar solo uno, el otro lo vuelve a levantar
    # solo y la pagina sigue respondiendo aunque se haya presionado Detener
    # (visto en la practica el 2026-07-14). Al desactivar el reloader queda
    # un solo proceso, que se cierra de verdad con Stop-Process.
    app.run(host="0.0.0.0", port=5001, debug=True, threaded=True, use_reloader=False)
