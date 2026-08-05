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
from flask import Blueprint, redirect, render_template, request, url_for

import db

bp = Blueprint("tickets", __name__)


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
    return redirect(url_for("ficha", equipo_id=equipo_id))


@bp.route("/tickets/<int:ticket_id>/estado", methods=["POST"])
def ticket_estado(ticket_id):
    estado = request.form["estado"]
    db.update_ticket_estado(ticket_id, estado)
    if request.form.get("origen") == "tickets":
        return redirect(url_for("tickets.tickets"))
    equipo_id = request.form.get("equipo_id")
    return redirect(url_for("ficha", equipo_id=equipo_id))


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
