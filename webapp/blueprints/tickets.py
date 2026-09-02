"""Tickets de soporte por equipo -- segundo modulo movido fuera de app.py
(ver blueprints/disponibilidad.py para el primero, mas chico). Las 3 rutas
de tickets vivian separadas en distintos puntos de app.py; quedan juntas
aca porque son el mismo dominio.

OJO al moverlo: los endpoints pasan a llamarse "tickets.create_ticket",
"tickets.ticket_estado" y "tickets.tickets" (el Blueprint antepone su
nombre) -- los url_for() en ficha.html, tickets.html e index.html, y el
redirect interno a "tickets" en ticket_estado(), se actualizaron para
apuntar al endpoint calificado. url_for('ficha', ...) NO cambia: esa ruta
se quedo en app.py.
"""
from flask import Blueprint, make_response, redirect, render_template, request, url_for

import db

bp = Blueprint("tickets", __name__)


def _responder_tras_ticket(equipo_id):
    """Decide como responder despues de crear/mover un ticket, segun desde
    donde se hizo -- hay 3 lugares que pueden crear/mover tickets ahora
    (antes solo existia Ficha completa):

    - Panel lateral de la pantalla frontal (index): llega por fetch con
      X-Requested-With, no se redirige a ningun lado -- se devuelve el mismo
      fragmento del panel ya actualizado, para no navegar y no perder el
      scroll/filtro de donde se estaba (mismo patron que ficha() en app.py).
    - Acordeon inline de Inventario de Equipos (origen=inventario): POST
      normal, pero se vuelve a Inventario en vez de a Ficha completa, con
      esa fila reabierta sola (mismo mecanismo que ya usa el choque de IP
      al editar -- ver admin.admin_equipos()/error_equipo_id).
    - Cualquier otro caso (Ficha completa, que no manda "origen" en el
      ticket): se mantiene el comportamiento de siempre, volver a esa ficha.

    Import diferido de app.py: evita import circular (app.py importa este
    blueprint antes de terminar de definirse a si mismo)."""
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        from app import _render_panel_editar_equipo
        html = _render_panel_editar_equipo(equipo_id)
        resp = make_response(html if html is not None else "")
        resp.headers["X-Netwatch-Guardado"] = "1"
        return resp
    if request.form.get("origen") == "inventario":
        return redirect(url_for("admin.admin_equipos", equipo_id=equipo_id))
    return redirect(url_for("ficha", equipo_id=equipo_id))


@bp.route("/equipo/<int:equipo_id>/tickets", methods=["POST"])
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
    return _responder_tras_ticket(equipo_id)


@bp.route("/tickets/<int:ticket_id>/estado", methods=["POST"])
def ticket_estado(ticket_id):
    # OJO -- request.form.get() y no request.form["estado"]: este campo solo
    # llega si el boton que se aprieta (Abierto/En progreso/Resolver, ver
    # templates/_panel_editar_equipo.html) trae su name/value en el POST. El
    # 2026-09-02 un bug de JS en el panel lateral (netwatchGuardarPanelAjax
    # armaba el FormData sin el boton que disparo el submit -- ver
    # netwatchFormData en static/js/pages/inventario.js) mandaba el POST sin
    # 'estado' y esto tiraba BadRequestKeyError, mostrando el debugger de
    # Flask entero a quien solo quiso cerrar un ticket. Ya se arreglo el JS,
    # pero esta ruta queda tolerante igual -- si por lo que sea vuelve a
    # faltar, no se cambia nada y se vuelve a donde estaba en vez de romper.
    estado = request.form.get("estado")
    if estado:
        db.update_ticket_estado(ticket_id, estado)
    if request.form.get("origen") == "tickets":
        return redirect(url_for("tickets.tickets"))
    equipo_id = request.form.get("equipo_id")
    if equipo_id:
        return _responder_tras_ticket(int(equipo_id))
    return redirect(url_for("tickets.tickets"))


@bp.route("/tickets")
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
