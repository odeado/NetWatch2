"""Exportacion de inventario/infraestructura a CSV/Excel -- tercer modulo
movido fuera de app.py (ver blueprints/disponibilidad.py para el patron).
Autocontenido: ninguna de las 3 rutas usa redirect/url_for, asi que no hay
referencias cruzadas que actualizar en otros modulos -- solo los url_for()
en admin_equipos.html y topologia.html que apuntaban al endpoint plano
pasaron a "export.<nombre>".
"""
import csv
import io
from datetime import datetime

from flask import Blueprint, Response

import db

bp = Blueprint("export", __name__)


@bp.route("/export/equipos.csv")
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


@bp.route("/export/equipos_formateado.xls")
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


@bp.route("/export/infraestructura_formateado.xls")
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
        "fortinet": ("#fef3c7", "#b45309"), "conversor": ("#ccfbf1", "#0f766e"),
        "modem": ("#fef9c3", "#854d0e"), "otro": ("#e5e7eb", "#374151"),
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
