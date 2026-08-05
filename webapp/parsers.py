"""Parseo de archivos externos (HTML-como-Excel y .xlsx real) hacia listas de
filas normalizadas -- movido fuera de app.py porque lo usan rutas de dos
dominios distintos (equipos/empleados en admin.py, infraestructura en
topologia.py), asi que ninguno de los dos puede ser "dueno" del archivo sin
crear un import circular con el otro. Pura logica de parseo, sin Flask ni
db -- no le importa quien lo llama.
"""
import re
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from html.parser import HTMLParser


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
