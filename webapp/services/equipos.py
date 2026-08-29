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
from pathlib import Path
import uuid

import db

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
