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

import csv
import io
import json
import re
import time
import unicodedata
import uuid
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

from flask import Flask, Response, jsonify, redirect, render_template, request, url_for

import db
import firebase_sync

BASE_DIR = Path(__file__).resolve().parent
RESULTS_DIR = BASE_DIR.parent / "scanner" / "results"
LEGACY_CONFIRM_FILE = BASE_DIR / "confirmations.json"
UPLOAD_DIR = BASE_DIR / "static" / "uploads"
MONITOR_LOG_FILE = BASE_DIR / "monitor.log"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
ALLOWED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

app = Flask(__name__)
db.init_db()


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


def _row_with_ports(row):
    row = dict(row)
    row["open_ports"] = json.loads(row["open_ports"] or "[]")
    return row


class _TablaHtmlParser(HTMLParser):
    """Lee la unica tabla de un archivo HTML-como-Excel (el truco clasico de
    exportar con extension .xls que Excel abre igual) y devuelve una lista de
    filas, cada una una lista de textos de celda. 100% libreria estandar, sin
    depender de openpyxl/xlrd/pandas para este formato en particular."""

    def __init__(self):
        super().__init__()
        self.filas = []
        self._fila_actual = None
        self._en_celda = False
        self._celda_actual = []

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._fila_actual = []
        elif tag in ("td", "th"):
            self._en_celda = True
            self._celda_actual = []
        elif tag == "br" and self._en_celda:
            self._celda_actual.append(" ")

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self._en_celda = False
            if self._fila_actual is not None:
                self._fila_actual.append("".join(self._celda_actual).strip())
        elif tag == "tr" and self._fila_actual is not None:
            self.filas.append(self._fila_actual)
            self._fila_actual = None

    def handle_data(self, data):
        if self._en_celda:
            self._celda_actual.append(data)


# Mapeo de encabezados del archivo externo -> nombres de campo internos.
# "Branch" queda sin mapear a proposito: "Location" es el campo que en la
# practica viene mas completo para la sucursal (decision tomada con Andres).
_INVENTARIO_HEADER_MAP = {
    "hostname": "hostname", "ip": "ip", "mac": "mac", "os": "os", "office": "office",
    "antivirus": "antivirus", "status": "_status_raw", "rdp habilitado": "_rdp_raw",
    "responsable": "responsable", "cargo": "cargo", "city": "ciudad",
    "department": "departamento", "brand": "marca", "model": "modelo",
    "serial number": "numero_serie", "location": "sucursal",
}


def _parsear_inventario_html(contenido):
    """Convierte el HTML de la tabla exportada en la lista de filas normalizadas
    que espera db.importar_inventario_masivo()."""
    parser = _TablaHtmlParser()
    parser.feed(contenido)
    if not parser.filas:
        return []

    encabezados = [h.strip().lower() for h in parser.filas[0]]
    columnas = [_INVENTARIO_HEADER_MAP.get(h) for h in encabezados]

    filas_normalizadas = []
    for fila_cruda in parser.filas[1:]:
        if not any(c.strip() for c in fila_cruda if c):
            continue
        fila = {}
        for idx, valor in enumerate(fila_cruda):
            if idx >= len(columnas) or not columnas[idx]:
                continue
            fila[columnas[idx]] = valor.strip() if valor else None

        estado_raw = (fila.pop("_status_raw", None) or "").strip().lower()
        if estado_raw == "online":
            fila["en_linea"] = True
        elif estado_raw == "offline":
            fila["en_linea"] = False
        else:
            fila["en_linea"] = None

        rdp_raw = (fila.pop("_rdp_raw", None) or "").strip().lower()
        fila["has_rdp"] = rdp_raw in ("si", "sí", "yes", "true")

        if fila.get("ip"):
            filas_normalizadas.append(fila)

    return filas_normalizadas


def _sin_acentos(texto):
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


# Igual que con equipos: mapeo de encabezados -> campos internos. Estos
# archivos suelen traer una fila de titulo (colspan) y una fila espaciadora
# antes del encabezado real, asi que _parsear_infraestructura_html busca la
# fila de encabezado en vez de asumir que es la primera.
_INFRA_HEADER_MAP = {
    "ciudad": "ciudad", "lugar": "sucursal", "piso": "piso", "observaciones": "observaciones",
    "marca": "marca", "modelo": "modelo", "bocas": "bocas", "direccion mac": "mac",
    "numero de serie": "numero_serie", "direccion ip": "ip", "enlace": "enlace", "estado": "estado",
}


def _parsear_infraestructura_html(contenido):
    """Convierte el HTML de la tabla de infraestructura exportada en la lista
    de filas normalizadas que espera db.importar_infraestructura_masiva()."""
    parser = _TablaHtmlParser()
    parser.feed(contenido)
    if not parser.filas:
        return []

    header_idx, columnas = None, None
    for i, fila_cruda in enumerate(parser.filas):
        claves = [_sin_acentos(c.strip().lower()) for c in fila_cruda]
        candidatas = [_INFRA_HEADER_MAP.get(c) for c in claves]
        if sum(1 for c in candidatas if c) >= 4:
            header_idx, columnas = i, candidatas
            break
    if header_idx is None:
        return []

    filas_normalizadas = []
    for fila_cruda in parser.filas[header_idx + 1:]:
        if not any(c.strip() for c in fila_cruda if c):
            continue
        fila = {}
        for idx, valor in enumerate(fila_cruda):
            if idx >= len(columnas) or not columnas[idx]:
                continue
            valor = valor.strip() if valor else None
            if valor in ("—", "-", ""):
                valor = None
            fila[columnas[idx]] = valor
        if fila:
            filas_normalizadas.append(fila)

    return filas_normalizadas


# Directorio de Responsables: encabezado del archivo -> campo interno.
# "Estado" se descarta a propósito porque en este archivo es un duplicado
# exacto de "Lugar de Trabajo" (verificado fila por fila), no un flag de
# activo/inactivo.
_EMPLEADOS_HEADER_MAP = {
    "nombre completo": "nombre", "email": "correo", "departamento": "departamento",
    "ciudad": "ciudad", "telefono": "telefono", "lugar de trabajo": "lugar_trabajo",
    "vpn activa": "_vpn_raw", "tipo de vpn": "tipo_vpn", "cargo": "cargo",
    "sistemas autorizados": "sistemas_autorizados",
}


def _parsear_empleados_html(contenido):
    """Convierte el HTML de la tabla de empleados exportada en la lista de
    filas normalizadas que espera db.importar_empleados_masivo()."""
    parser = _TablaHtmlParser()
    parser.feed(contenido)
    if not parser.filas:
        return []

    encabezados = [_sin_acentos(h.strip().lower()) for h in parser.filas[0]]
    columnas = [_EMPLEADOS_HEADER_MAP.get(h) for h in encabezados]

    filas_normalizadas = []
    for fila_cruda in parser.filas[1:]:
        if not any(c.strip() for c in fila_cruda if c):
            continue
        fila = {}
        for idx, valor in enumerate(fila_cruda):
            if idx >= len(columnas) or not columnas[idx]:
                continue
            fila[columnas[idx]] = valor.strip() if valor else None

        vpn_raw = (fila.pop("_vpn_raw", None) or "").strip().lower()
        fila["vpn_activa"] = vpn_raw in ("si", "sí", "yes", "true")

        if fila.get("nombre"):
            filas_normalizadas.append(fila)

    return filas_normalizadas


# --- Importacion desde otro sistema de gestion (archivo .xlsx real) --------
# A diferencia de los importadores de arriba (que leen un .xls que en
# realidad es una tabla HTML, el truco clasico de "Exportar a Excel"), este
# archivo es un .xlsx de verdad -- un zip con XML adentro. Para no sumar
# openpyxl/pandas como dependencia nueva, se lee con zipfile + xml.etree
# (100% libreria estandar), sacando solo lo que necesitamos: la lista de
# hojas, el diccionario de textos compartidos y las celdas de cada fila.
_XLSX_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_XLSX_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _col_letras_a_indice(ref):
    """'C7' -> parte de letras 'C' -> indice de columna 0-based (2)."""
    letras = "".join(c for c in ref if c.isalpha())
    idx = 0
    for c in letras:
        idx = idx * 26 + (ord(c.upper()) - ord("A") + 1)
    return idx - 1


def _leer_xlsx(archivo_like):
    """Lee un .xlsx real y devuelve {nombre_hoja: [[valor_celda, ...], ...]},
    con la primera fila de cada hoja como encabezado (misma convencion que
    _TablaHtmlParser.filas, para poder reusar el mismo estilo de parseo)."""
    with zipfile.ZipFile(archivo_like) as z:
        nombres = z.namelist()
        wb_xml = ET.fromstring(z.read("xl/workbook.xml"))
        rel_por_id = {}
        if "xl/_rels/workbook.xml.rels" in nombres:
            rels_xml = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
            for rel in rels_xml:
                rel_por_id[rel.attrib["Id"]] = rel.attrib["Target"]

        shared = []
        if "xl/sharedStrings.xml" in nombres:
            ss_xml = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in ss_xml.findall("m:si", _XLSX_NS):
                shared.append("".join((t.text or "") for t in si.findall(".//m:t", _XLSX_NS)))

        hojas = []
        sheets_el = wb_xml.find("m:sheets", _XLSX_NS)
        for sheet in (sheets_el if sheets_el is not None else []):
            nombre = sheet.attrib.get("name", "")
            rid = sheet.attrib.get(f"{_XLSX_REL_NS}id")
            target = rel_por_id.get(rid, "")
            ruta = target if target.startswith("xl/") else f"xl/{target}"
            hojas.append((nombre, ruta))

        resultado = {}
        for nombre, ruta in hojas:
            if ruta not in nombres:
                continue
            sheet_xml = ET.fromstring(z.read(ruta))
            sheet_data = sheet_xml.find("m:sheetData", _XLSX_NS)
            filas = []
            for row in (sheet_data if sheet_data is not None else []):
                celdas = {}
                max_idx = -1
                for c in row.findall("m:c", _XLSX_NS):
                    ref = c.attrib.get("r", "")
                    idx = _col_letras_a_indice(ref) if ref else (max_idx + 1)
                    tipo = c.attrib.get("t")
                    v = c.find("m:v", _XLSX_NS)
                    valor = None
                    if tipo == "s":
                        if v is not None and v.text is not None:
                            valor = shared[int(v.text)]
                    elif tipo == "inlineStr":
                        is_el = c.find("m:is", _XLSX_NS)
                        if is_el is not None:
                            valor = "".join((t.text or "") for t in is_el.findall(".//m:t", _XLSX_NS))
                    elif tipo == "b":
                        valor = (v.text == "1") if v is not None else None
                    else:
                        if v is not None and v.text is not None:
                            valor = v.text
                    celdas[idx] = valor
                    max_idx = max(max_idx, idx)
                filas.append([celdas.get(i) for i in range(max_idx + 1)] if max_idx >= 0 else [])
            resultado[nombre] = filas
        return resultado


_IP_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")

# Sistema externo de gestion de usuarios/equipos: encabezado -> campo interno.
_GESTION_USUARIOS_HEADER_MAP = {
    "nombre": "nombre", "correo": "correo", "departamento": "departamento",
    "estado": "_estado_raw", "ciudad": "ciudad", "tipo vpn": "tipo_vpn",
}
_GESTION_EQUIPOS_HEADER_MAP = {
    "nombre": "hostname", "estado": "_estado_raw", "marca": "marca", "modelo": "modelo",
    "n° serie": "numero_serie", "ciudad": "ciudad", "lugar": "sucursal",
    "procesador": "cpu", "ram": "ram", "disco duro": "almacenamiento",
    "tarjeta grafica": "gpu", "windows": "os", "office": "office", "antivirus": "antivirus",
    "ips": "_ip_raw", "descripcion": "descripcion", "usuarios asignados": "_responsables_raw",
}


def _parsear_gestion_usuarios(filas):
    """Hoja 'Usuarios' del sistema externo -> filas normalizadas para
    db.importar_gestion_masiva(). 'Estado' ahi es Teletrabajo/Trabajando
    (ambos activos, solo cambia si es remoto o presencial) o Eliminado
    (usuario dado de baja en el sistema de origen)."""
    if not filas:
        return []
    encabezados = [_sin_acentos((h or "").strip().lower()) for h in filas[0]]
    columnas = [_GESTION_USUARIOS_HEADER_MAP.get(h) for h in encabezados]

    normalizadas = []
    for fila_cruda in filas[1:]:
        if not any((c or "").strip() for c in fila_cruda if isinstance(c, str)):
            continue
        fila = {}
        for idx, valor in enumerate(fila_cruda):
            if idx >= len(columnas) or not columnas[idx]:
                continue
            fila[columnas[idx]] = valor.strip() if isinstance(valor, str) else valor

        estado_raw = (fila.pop("_estado_raw", None) or "").strip().lower()
        if estado_raw == "eliminado":
            fila["activo"] = False
        elif estado_raw == "teletrabajo":
            fila["activo"] = True
            fila["lugar_trabajo"] = "Remoto"
        elif estado_raw == "trabajando":
            fila["activo"] = True
            fila["lugar_trabajo"] = "Presencial"
        else:
            fila["activo"] = None

        if fila.get("correo") and fila["correo"].strip().lower() == "ninguno":
            fila["correo"] = None
        if fila.get("tipo_vpn") and fila["tipo_vpn"].strip().lower() == "ninguna":
            fila["tipo_vpn"] = None

        if fila.get("nombre"):
            normalizadas.append(fila)
    return normalizadas


def _parsear_gestion_equipos(filas):
    """Hoja 'Equipos' del sistema externo -> filas normalizadas. Muchos
    equipos ahi no tienen IP fija (columna 'IPs' en 'Dinamica' -- notebooks
    remotos/VPN), asi que la IP se descarta si no calza con el formato real
    y el importador matchea por hostname en ese caso."""
    if not filas:
        return []
    encabezados = [_sin_acentos((h or "").strip().lower()) for h in filas[0]]
    columnas = [_GESTION_EQUIPOS_HEADER_MAP.get(h) for h in encabezados]

    normalizadas = []
    for fila_cruda in filas[1:]:
        if not any((c or "").strip() for c in fila_cruda if isinstance(c, str)):
            continue
        fila = {}
        for idx, valor in enumerate(fila_cruda):
            if idx >= len(columnas) or not columnas[idx]:
                continue
            fila[columnas[idx]] = valor.strip() if isinstance(valor, str) else valor

        ip_raw = (fila.pop("_ip_raw", None) or "").strip()
        fila["ip"] = ip_raw if _IP_RE.match(ip_raw) else None

        responsables_raw = fila.pop("_responsables_raw", None) or ""
        fila["responsables"] = [n.strip() for n in responsables_raw.split(",") if n.strip()]

        fila["estado"] = (fila.pop("_estado_raw", None) or "").strip()

        if fila.get("hostname"):
            normalizadas.append(fila)
    return normalizadas


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

    resumen_importacion = None
    if request.args.get("importado") == "1":
        resumen_importacion = {
            "creados": int(request.args.get("creados", 0)),
            "actualizados": int(request.args.get("actualizados", 0)),
            "sin_cambios": int(request.args.get("sin_cambios", 0)),
            "omitidos": int(request.args.get("omitidos", 0)),
            "total": int(request.args.get("total", 0)),
        }

    return render_template(
        "admin_equipos.html",
        equipos=equipos,
        scan_files=scan_files,
        usuarios=usuarios,
        error=request.args.get("error"),
        resumen_importacion=resumen_importacion,
        active_tab="equipos",
        hoy=datetime.now().strftime("%Y-%m-%d"),
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


@app.route("/equipo/<int:equipo_id>", methods=["GET", "POST"])
def ficha(equipo_id):
    if request.method == "POST":
        fields = {k: (request.form.get(k, "").strip() or None) for k in db.FICHA_FIELDS}
        fields["critico"] = 1 if request.form.get("critico") == "on" else 0
        fields["gestionado"] = 1 if request.form.get("gestionado") == "on" else 0

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

        db.update_ficha(equipo_id, fields)
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
    return render_template(
        "ficha.html", e=equipo, tickets=tickets, rdp_history=rdp_history, usuarios=usuarios,
        dispositivos=dispositivos, disponibilidad=disponibilidad,
    )


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


@app.route("/equipo/<int:equipo_id>/tickets", methods=["POST"])
def create_ticket(equipo_id):
    titulo = request.form.get("titulo", "").strip()
    if titulo:
        db.create_ticket(
            equipo_id,
            titulo,
            request.form.get("descripcion", "").strip() or None,
            request.form.get("prioridad", "normal"),
            request.form.get("asignado_a", "").strip() or None,
        )
    return redirect(url_for("ficha", equipo_id=equipo_id))


@app.route("/tickets/<int:ticket_id>/estado", methods=["POST"])
def ticket_estado(ticket_id):
    estado = request.form["estado"]
    db.update_ticket_estado(ticket_id, estado)
    if request.form.get("origen") == "tickets":
        return redirect(url_for("tickets"))
    equipo_id = request.form.get("equipo_id")
    return redirect(url_for("ficha", equipo_id=equipo_id))


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


def _dispositivos_con_puertos():
    """Arma la lista de dispositivos de red con sus puertos ya resueltos
    (que equipo o que otro dispositivo tiene conectado cada boca). Comparten
    esta logica tanto la vista interactiva de Topologia como el resumen
    imprimible."""
    dispositivos = db.list_dispositivos()
    equipos_por_dispositivo = db.list_equipos_por_dispositivo()
    conexiones_por_dispositivo = db.list_conexiones_dispositivos()
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
        ocupados_dispositivo = dict(conexiones_por_dispositivo.get(d["id"], {}))

        puertos = db.get_puertos_definicion(d)
        for p in puertos:
            p["equipo"] = ocupados_equipo.pop(p["label"], None)
            destino_id = ocupados_dispositivo.pop(p["label"], None)
            p["dispositivo_destino"] = dispositivos_by_id.get(destino_id) if destino_id else None
        d["puertos"] = puertos
        # equipos con un puerto asignado que no calza con la grilla de bocas definida
        # (ej. si todavia no se configuro plantilla/bocas, o texto libre viejo)
        d["puertos_fuera_de_grilla"] = list(ocupados_equipo.values())

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


@app.route("/topologia")
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


@app.route("/topologia/importar", methods=["POST"])
def importar_infraestructura():
    """Importa un inventario de infraestructura externo (switches/modems/
    routers ya escritos a mano en otro archivo, guardado como tabla HTML con
    extension .xls). Matchea por IP/MAC/N.Serie; si encuentra el dispositivo
    el archivo manda, si no lo crea infiriendo tipo y plantilla de puertos."""
    archivo = request.files.get("archivo")
    if not archivo or not archivo.filename:
        return redirect(url_for("topologia", error="archivo_requerido"))

    contenido = archivo.read().decode("utf-8", errors="replace")
    filas = _parsear_infraestructura_html(contenido)
    if not filas:
        return redirect(url_for("topologia", error="archivo_sin_filas"))

    resumen = db.importar_infraestructura_masiva(filas)
    return redirect(url_for(
        "topologia", importado="1",
        creados=resumen["creados"], actualizados=resumen["actualizados"], total=resumen["total"],
    ))


@app.route("/topologia/resumen")
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
    "switch": "#34d399",
    "router": "#60a5fa",
    "fortinet": "#fbbf24",
    "otro": "#9aa3b8",
}
TIPO_DISPOSITIVO_SIGLA = {
    "switch": "SW",
    "router": "RT",
    "fortinet": "FW",
    "otro": "?",
}


def _anchor_puerto(nodo, otro):
    """Punto de anclaje en el borde del nodo mas cercano al otro nodo, para
    que las lineas de conexion salgan del borde de la caja y no de su centro."""
    cx, cy = nodo["x"] + nodo["w"] / 2, nodo["y"] + nodo["h"] / 2
    ocx, ocy = otro["x"] + otro["w"] / 2, otro["y"] + otro["h"] / 2
    dx, dy = ocx - cx, ocy - cy
    if abs(dy) >= abs(dx):
        return cx, (nodo["y"] + nodo["h"] if dy > 0 else nodo["y"])
    return (nodo["x"] + nodo["w"] if dx > 0 else nodo["x"]), cy


@app.route("/topologia/diagrama")
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

    ciudad_filtro = request.args.get("ciudad", "todos")
    if ciudad_filtro and ciudad_filtro != "todos":
        dispositivos = [
            d for d in todos_dispositivos
            if (d.get("ciudad") or "Sin ciudad asignada") == ciudad_filtro
        ]
    else:
        dispositivos = todos_dispositivos

    grupos = {}
    for d in dispositivos:
        clave = d.get("ciudad") or "Sin ciudad asignada"
        grupos.setdefault(clave, []).append(d)

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

    nodos = {}
    grupos_layout = []
    fila_y = margin + grupo_pad
    max_cols = 1
    for ciudad, disps in grupos.items():
        col_x = margin + grupo_pad
        fila_nodos = []
        for d in disps:
            equipos_conectados = [p["equipo"] for p in d["puertos"] if p.get("equipo")]
            equipos_conectados.sort(key=lambda e: bool(e.get("en_linea")))
            equipos_detalle = [{
                "hostname": e.get("hostname") or e.get("ip"),
                "ip": e.get("ip"),
                "en_linea": bool(e.get("en_linea")),
                "has_rdp": bool(e.get("has_rdp")),
                "responsable": e.get("responsable"),
            } for e in equipos_conectados]
            nodo = {
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
                "x": col_x,
                "w": node_w,
                "h": node_h,
                "equipos_total": len(equipos_conectados),
                "equipos_offline": sum(1 for e in equipos_conectados if not e.get("en_linea")),
                "equipos_dots": equipos_conectados[:20],
                "equipos_extra": max(0, len(equipos_conectados) - 20),
                "equipos_detalle": equipos_detalle,
                "enlaces_detalle": [],
            }
            nodos[d["id"]] = nodo
            fila_nodos.append(nodo)
            col_x += node_w + col_gap
        cols_en_fila = len(disps)
        max_cols = max(max_cols, cols_en_fila)
        fila_extra = max((_extra_h(len(n["equipos_detalle"])) for n in fila_nodos), default=0)
        for n in fila_nodos:
            n["y"] = fila_y
            n["drawer_y"] = fila_y + node_h + drawer_top_pad
        grupos_layout.append({
            "nombre": ciudad,
            "x": margin,
            "y": fila_y - grupo_pad,
            "w": cols_en_fila * node_w + (cols_en_fila - 1) * col_gap + grupo_pad * 2,
            "h": node_h + fila_extra + grupo_pad * 2 - 10,
        })
        fila_y += node_h + fila_extra + row_gap

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

    ancho = max((max_cols * (node_w + col_gap)) + margin * 2, 560)
    alto = fila_y + margin

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
    )


def _int_from_form(field):
    raw = request.form.get(field, "").strip()
    return int(raw) if raw.isdigit() else None


@app.route("/topologia/dispositivos", methods=["POST"])
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
    return redirect(url_for("topologia"))


@app.route("/topologia/dispositivos/<int:dispositivo_id>", methods=["POST"])
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
    return redirect(url_for("topologia"))


@app.route("/topologia/dispositivos/<int:dispositivo_id>/puertos", methods=["POST"])
def asignar_puerto(dispositivo_id):
    puerto = request.form.get("puerto", "").strip()
    destino = request.form.get("destino", "")
    destino_tipo, destino_id = "", None
    if destino.startswith("equipo:"):
        destino_tipo, destino_id = "equipo", int(destino.split(":", 1)[1])
    elif destino.startswith("dispositivo:"):
        destino_tipo, destino_id = "dispositivo", int(destino.split(":", 1)[1])
    if puerto:
        db.set_puerto_destino(dispositivo_id, puerto, destino_tipo, destino_id)
    return redirect(url_for("topologia"))


@app.route("/export/equipos.csv")
def export_equipos_csv():
    equipos = db.list_equipos_export()
    ticket_counts = db.get_open_ticket_counts()

    output = io.StringIO()
    output.write("﻿")  # BOM para que Excel muestre bien los acentos
    writer = csv.writer(output)
    writer.writerow([
        "IP", "Subred", "Hostname", "MAC", "En linea", "Estado deteccion",
        "Confianza", "Responsable", "Correo responsable",
        "Marca", "Modelo", "N. Serie", "Fecha adquisicion", "Garantia hasta",
        "Sucursal", "Ciudad", "Departamento",
        "CPU", "RAM", "Almacenamiento", "GPU", "Placa madre",
        "Estado ciclo de vida", "Critico", "Gestionado",
        "Dispositivo de red", "Puerto", "Tickets abiertos", "Notas",
    ])
    for e in equipos:
        confianza = f"{e.get('confidence_label') or ''} ({e.get('confidence_score') or ''})".strip()
        writer.writerow([
            e.get("ip"), e.get("subred"), e.get("hostname"), e.get("mac"),
            "Si" if e.get("en_linea") else "No", e.get("estado_deteccion"),
            confianza,
            e.get("responsable"), e.get("correo_responsable"),
            e.get("marca"), e.get("modelo"), e.get("numero_serie"),
            e.get("fecha_adquisicion"), e.get("garantia_hasta"),
            e.get("sucursal"), e.get("ciudad"), e.get("departamento"),
            e.get("cpu"), e.get("ram"), e.get("almacenamiento"), e.get("gpu"), e.get("placa_madre"),
            e.get("estado_ciclo_vida"),
            "Si" if e.get("critico") else "No",
            "Si" if e.get("gestionado") else "No",
            e.get("dispositivo_nombre"), e.get("puerto"),
            ticket_counts.get(e["id"], 0),
            e.get("notas"),
        ])

    filename = "netwatch_inventario_" + datetime.now().strftime("%Y%m%d_%H%M") + ".csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/export/equipos_formateado.xls")
def export_equipos_excel():
    """Mismo inventario que el CSV, pero como tabla HTML-Excel con colores
    (abre directo en Excel con formato, ideal para compartir/imprimir sin
    tener que aplicar formato condicional a mano cada vez)."""
    equipos = db.list_equipos_export()
    ticket_counts = db.get_open_ticket_counts()

    def esc(v):
        if v is None:
            return ""
        return (str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    columnas = [
        "Hostname", "IP", "MAC", "Estado Red", "Deteccion", "Responsable", "Sucursal",
        "Ciudad", "Departamento", "Marca", "Modelo", "N. Serie", "OS", "Office",
        "Antivirus", "Dispositivo de red", "Puerto", "Tickets abiertos", "Critico", "Notas",
    ]

    filas_html = []
    for e in equipos:
        en_linea = bool(e.get("en_linea"))
        origen_manual = e.get("origen") == "manual"
        tickets_abiertos = ticket_counts.get(e["id"], 0)
        critico = bool(e.get("critico"))

        if origen_manual:
            estado_html = '<td style="color:#6b7280;">Sin monitoreo</td>'
        elif en_linea:
            estado_html = '<td style="background-color:#d1fae5;color:#065f46;font-weight:bold;">online</td>'
        else:
            estado_html = '<td style="background-color:#fee2e2;color:#991b1b;font-weight:bold;">offline</td>'

        tickets_html = (
            f'<td style="background-color:#fef3c7;color:#b45309;font-weight:bold;">{tickets_abiertos}</td>'
            if tickets_abiertos else f'<td>{tickets_abiertos}</td>'
        )
        critico_html = (
            '<td style="background-color:#fee2e2;color:#991b1b;font-weight:bold;">Si</td>' if critico else "<td>No</td>"
        )

        celdas = [
            f"<td>{esc(e.get('hostname'))}</td>",
            f"<td>{esc(e.get('ip'))}</td>",
            f"<td>{esc(e.get('mac'))}</td>",
            estado_html,
            f"<td>{esc(e.get('estado_deteccion'))}</td>",
            f"<td>{esc(e.get('responsable'))}</td>",
            f"<td>{esc(e.get('sucursal'))}</td>",
            f"<td>{esc(e.get('ciudad'))}</td>",
            f"<td>{esc(e.get('departamento'))}</td>",
            f"<td>{esc(e.get('marca'))}</td>",
            f"<td>{esc(e.get('modelo'))}</td>",
            f"<td>{esc(e.get('numero_serie'))}</td>",
            f"<td>{esc(e.get('os'))}</td>",
            f"<td>{esc(e.get('office'))}</td>",
            f"<td>{esc(e.get('antivirus'))}</td>",
            f"<td>{esc(e.get('dispositivo_nombre'))}</td>",
            f"<td>{esc(e.get('puerto'))}</td>",
            tickets_html,
            critico_html,
            f"<td>{esc(e.get('notas'))}</td>",
        ]
        filas_html.append("<tr>" + "".join(celdas) + "</tr>")

    generado_en = datetime.now().strftime("%d-%m-%Y %H:%M")
    html = f"""
      <html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:x="urn:schemas-microsoft-com:office:excel" xmlns="http://www.w3.org/TR/REC-html40">
      <head>
        <meta charset="utf-8"/>
        <!--[if gte mso 9]>
        <xml>
          <x:ExcelWorkbook>
            <x:ExcelWorksheets>
              <x:ExcelWorksheet>
                <x:Name>Inventario de Equipos</x:Name>
                <x:WorksheetOptions><x:DisplayGridlines/></x:WorksheetOptions>
              </x:ExcelWorksheet>
            </x:ExcelWorksheets>
          </x:ExcelWorkbook>
        </xml>
        <![endif]-->
        <style>
          table {{ border-collapse: collapse; font-family: Segoe UI, sans-serif; font-size: 12px; }}
          th {{ background-color: #2563eb; color: white; font-weight: bold; border: 1px solid #d1d5db; padding: 6px 8px; text-align: left; }}
          td {{ border: 1px solid #e5e7eb; padding: 5px 8px; }}
          .title {{ font-size: 16px; font-weight: bold; color: #1a1f2b; padding-bottom: 4px; }}
          .subtitle {{ font-size: 11px; color: #6b7280; padding-bottom: 10px; }}
        </style>
      </head>
      <body>
        <div class="title">Win NetWatch RMM - Inventario Completo de Equipos</div>
        <div class="subtitle">Generado el {generado_en} &mdash; {len(equipos)} equipos</div>
        <table>
          <thead><tr>{"".join(f"<th>{c}</th>" for c in columnas)}</tr></thead>
          <tbody>
            {"".join(filas_html)}
          </tbody>
        </table>
      </body>
      </html>
    """

    filename = "netwatch_inventario_" + datetime.now().strftime("%Y%m%d_%H%M") + ".xls"
    return Response(
        html,
        mimetype="application/vnd.ms-excel",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/export/infraestructura_formateado.xls")
def export_infraestructura_excel():
    """Inventario de infraestructura (switches/modems/routers) como tabla
    HTML-Excel con colores, mismo truco que el export de equipos."""
    dispositivos = db.list_dispositivos()

    def esc(v):
        if v is None:
            return ""
        return str(v).replace("&", "&amp;", ).replace("<", "&lt;").replace(">", "&gt;")

    columnas = [
        "Ciudad", "Sucursal", "Piso", "Nombre", "Tipo", "Marca", "Modelo",
        "Bocas", "MAC", "N. Serie", "IP", "Enlace", "Estado", "Notas",
    ]

    estado_colores = {
        "Nuevo": ("#d1fae5", "#065f46"), "Usado": ("#e5e7eb", "#374151"),
        "En reparacion": ("#fef3c7", "#b45309"), "Fuera de servicio": ("#fee2e2", "#991b1b"),
    }
    tipo_colores = {
        "switch": ("#d1fae5", "#065f46"), "router": ("#dbeafe", "#1d4ed8"),
        "fortinet": ("#fef3c7", "#b45309"), "otro": ("#e5e7eb", "#374151"),
    }

    filas_html = []
    for d in dispositivos:
        estado_bg, estado_fg = estado_colores.get(d.get("estado"), ("", "#374151"))
        estado_style = f'style="background-color:{estado_bg};color:{estado_fg};font-weight:bold;"' if estado_bg else ""
        tipo_bg, tipo_fg = tipo_colores.get(d.get("tipo"), ("", "#374151"))
        tipo_style = f'style="background-color:{tipo_bg};color:{tipo_fg};font-weight:bold;"' if tipo_bg else ""

        celdas = [
            f"<td>{esc(d.get('ciudad'))}</td>",
            f"<td>{esc(d.get('sucursal'))}</td>",
            f"<td>{esc(d.get('piso'))}</td>",
            f"<td>{esc(d.get('nombre'))}</td>",
            f"<td {tipo_style}>{esc(db.TIPO_DISPOSITIVO_LABELS.get(d.get('tipo'), d.get('tipo')))}</td>",
            f"<td>{esc(d.get('marca'))}</td>",
            f"<td>{esc(d.get('modelo'))}</td>",
            f"<td>{esc(d.get('cantidad_bocas'))}</td>",
            f"<td style=\"font-family:Consolas,monospace;font-size:10px;\">{esc(d.get('mac'))}</td>",
            f"<td style=\"font-family:Consolas,monospace;font-size:10px;\">{esc(d.get('numero_serie'))}</td>",
            f"<td style=\"font-family:Consolas,monospace;font-size:10px;\">{esc(d.get('ip'))}</td>",
            f"<td>{esc(d.get('enlace'))}</td>",
            f"<td {estado_style}>{esc(d.get('estado'))}</td>",
            f"<td>{esc(d.get('notas'))}</td>",
        ]
        filas_html.append("<tr>" + "".join(celdas) + "</tr>")

    generado_en = datetime.now().strftime("%d-%m-%Y %H:%M")
    html = f"""
      <html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:x="urn:schemas-microsoft-com:office:excel" xmlns="http://www.w3.org/TR/REC-html40">
      <head>
        <meta charset="utf-8"/>
        <!--[if gte mso 9]>
        <xml>
          <x:ExcelWorkbook>
            <x:ExcelWorksheets>
              <x:ExcelWorksheet>
                <x:Name>Infraestructura de Red</x:Name>
                <x:WorksheetOptions><x:DisplayGridlines/></x:WorksheetOptions>
              </x:ExcelWorksheet>
            </x:ExcelWorksheets>
          </x:ExcelWorkbook>
        </xml>
        <![endif]-->
        <style>
          table {{ border-collapse: collapse; font-family: Segoe UI, sans-serif; font-size: 12px; }}
          th {{ background-color: #1e293b; color: white; font-weight: bold; border: 1px solid #cbd5e1; padding: 6px 8px; text-align: left; }}
          td {{ border: 1px solid #e5e7eb; padding: 5px 8px; }}
          .title {{ font-size: 16px; font-weight: bold; color: #1a1f2b; padding-bottom: 4px; }}
          .subtitle {{ font-size: 11px; color: #6b7280; padding-bottom: 10px; }}
        </style>
      </head>
      <body>
        <div class="title">Win NetWatch RMM - Inventario de Infraestructura (Switches / Modems)</div>
        <div class="subtitle">Generado el {generado_en} &mdash; {len(dispositivos)} dispositivos</div>
        <table>
          <thead><tr>{"".join(f"<th>{c}</th>" for c in columnas)}</tr></thead>
          <tbody>
            {"".join(filas_html)}
          </tbody>
        </table>
      </body>
      </html>
    """

    filename = "netwatch_infraestructura_" + datetime.now().strftime("%Y%m%d_%H%M") + ".xls"
    return Response(
        html,
        mimetype="application/vnd.ms-excel",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/tickets")
def tickets():
    estado_filtro = request.args.get("estado") or None
    prioridad_filtro = request.args.get("prioridad") or None
    lista = db.list_all_tickets(estado_filtro, prioridad_filtro)
    return render_template(
        "tickets.html",
        tickets=lista,
        estado_filtro=estado_filtro,
        prioridad_filtro=prioridad_filtro,
        total_abiertos=db.count_open_tickets(),
    )


@app.route("/disponibilidad")
def disponibilidad():
    """Ranking de los equipos con peor disponibilidad -- para encontrar el
    que anda fallando seguido (varias caidas cortas) y no solo el que esta
    caido ahora mismo, que ya se ve de entrada en el inventario."""
    dias = request.args.get("dias", 30, type=int)
    if dias not in (7, 30, 90):
        dias = 30
    ranking = db.ranking_disponibilidad(dias=dias, limite=25)
    return render_template("disponibilidad.html", ranking=ranking, dias=dias)


if __name__ == "__main__":
    app.run(debug=True, port=5001, threaded=True)
