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

import json
import time
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, jsonify, make_response, redirect, render_template, request, url_for

import db
import firebase_sync
from services import equipos as equipos_service
from blueprints.admin import bp as admin_bp
from blueprints.disponibilidad import bp as disponibilidad_bp
from blueprints.export import bp as export_bp
from blueprints.tickets import bp as tickets_bp
from blueprints.topologia import bp as topologia_bp

BASE_DIR = Path(__file__).resolve().parent
RESULTS_DIR = BASE_DIR.parent / "scanner" / "results"
LEGACY_CONFIRM_FILE = BASE_DIR / "confirmations.json"
UPLOAD_DIR = BASE_DIR / "static" / "uploads"
MONITOR_LOG_FILE = BASE_DIR / "monitor.log"
SCANNER_CONFIG_FILE = BASE_DIR.parent / "scanner" / "config.json"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _cidr_por_nombre_sitio():
    """Lee scanner/config.json y arma {nombre_sitio: cidr} (ej. "iquique" ->
    "172.30.112.0/24") para que los pills de estado de Firebase en index.html
    (que muestran el nombre tal cual lo publica el sitio) se puedan usar
    para filtrar las fichas por esa subred con un clic. Tolera que el
    archivo no exista o este mal formado -- en ese caso los pills
    simplemente no filtran nada."""
    try:
        cfg = json.loads(SCANNER_CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {s["nombre"]: s["cidr"] for s in cfg.get("sitios_remotos", []) if s.get("nombre") and s.get("cidr")}

app = Flask(__name__)
db.init_db()
app.register_blueprint(admin_bp)
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
        subredes=db.list_subredes(),
        cidr_por_sitio=_cidr_por_nombre_sitio(),
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


@app.route("/equipos/nuevo", methods=["POST"])
def crear_equipo_manual():
    ip = request.form.get("ip", "").strip()
    if not ip:
        return redirect(url_for("admin.admin_equipos", error="ip_requerida"))
    if db.get_equipo_by_ip(ip):
        return redirect(url_for("admin.admin_equipos", error="ip_duplicada"))

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

    return redirect(url_for("admin.admin_equipos"))


@app.route("/import-todos", methods=["POST"])
def import_scan_todos():
    # Para cuando cada sucursal remota (Matta/Arica/Iquique) deja su propio
    # archivo de escaneo en la carpeta sincronizada (OneDrive/etc.) -- en vez
    # de importar uno por uno, este boton los aplica todos de una pasada.
    for target in db.list_scan_files(RESULTS_DIR):
        db.import_scan(target)
    if LEGACY_CONFIRM_FILE.exists():
        db.migrate_legacy_confirmations(LEGACY_CONFIRM_FILE)
    return redirect(url_for("admin.admin_equipos"))


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
    # Tickets de soporte: Andres pidio (2026-08-25) poder crear un ticket sin
    # salir de la pantalla frontal, igual que ya se podia desde Ficha completa
    # -- por eso el panel necesita la lista igual que ficha().
    tickets = db.list_tickets_for_equipo(equipo_id)
    return render_template(
        "_panel_editar_equipo.html", e=equipo, usuarios=usuarios, dispositivos=dispositivos,
        categorias_equipo=db.CATEGORIAS_EQUIPO, error=error,
        ip_conflicto=ip_conflicto, ip_nueva=ip_nueva, tickets=tickets,
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
        resultado = equipos_service.actualizar_ficha(equipo_id, request)
        if resultado is None:
            return redirect(url_for("index"))
        ip_error = resultado.ip_error
        ip_conflicto_id = resultado.ip_conflicto_id
        nueva_ip = resultado.ip_nueva

        # Si vino del panel lateral de la pantalla frontal (fetch con
        # X-Requested-With), no se redirige a ninguna pagina -- se devuelve
        # el mismo fragmento del formulario (con el error si lo hubo) y un
        # header propio para que el JS sepa si cerrar el panel o no, asi
        # nunca se navega ni se pierde el scroll/filtro de donde se estaba.
        if es_ajax:
            html = _render_panel_editar_equipo(
                equipo_id, error=ip_error, ip_conflicto_id=ip_conflicto_id,
                ip_nueva=nueva_ip,
            )
            resp = make_response(html)
            if not ip_error:
                resp.headers["X-Netwatch-Guardado"] = "1"
            return resp

        # "origen" dice desde donde se edito -- si vino del panel acordeon
        # inline de Gestion de Equipos, vuelve ahi en vez de mandar a la ficha
        # completa (asi no se sale de Inventario de Equipos y puede seguir
        # editando el siguiente equipo de la lista sin perder el lugar).
        # OJO -- 2026-08-27: el caso sin error mandaba a admin.admin_equipos
        # SIN equipo_id, o sea que al guardar se perdia la fila (volvia
        # cerrada y la pagina aterrizaba arriba de todo) -- justo lo
        # contrario de "seguir editando el siguiente sin perder el lugar".
        # Ahora se manda igual el equipo_id: error_equipo_id lo reabre solo
        # (mismo mecanismo que ya existia para el choque de IP) y el JS de
        # abajo hace scroll hasta ahi. Tambien se corrigio el endpoint del
        # caso con error, que apuntaba a "admin_equipos" sin el prefijo del
        # blueprint ("admin.admin_equipos") y tiraba BuildError.
        if request.form.get("origen") == "inventario":
            if ip_error:
                return redirect(url_for(
                    "admin.admin_equipos", error=ip_error, equipo_id=equipo_id,
                    ip_nueva=nueva_ip, ip_conflicto_id=ip_conflicto_id,
                ))
            return redirect(url_for("admin.admin_equipos", equipo_id=equipo_id))
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
        return redirect(url_for("admin.admin_equipos"))
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


if __name__ == "__main__":
    # use_reloader=False es a proposito: con el reloader activo, Flask lanza
    # DOS procesos python.exe (uno vigilante + uno de trabajo). Si "Detener
    # NetWatch.bat" alcanza a matar solo uno, el otro lo vuelve a levantar
    # solo y la pagina sigue respondiendo aunque se haya presionado Detener
    # (visto en la practica el 2026-07-14). Al desactivar el reloader queda
    # un solo proceso, que se cierra de verdad con Stop-Process.
    app.run(host="0.0.0.0", port=5001, debug=True, threaded=True, use_reloader=False)
