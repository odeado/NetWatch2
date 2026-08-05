"""Ranking de disponibilidad -- primer modulo movido fuera de app.py como
prueba del patron de Blueprints (ver AGENTS.md o el chat donde se decidio
el desglose). Una sola ruta, asi que sirve de ejemplo minimo antes de migrar
los modulos mas grandes (tickets, topologia, admin).

OJO al moverlo: el endpoint pasa a llamarse "disponibilidad.disponibilidad"
(el Blueprint antepone su nombre), asi que todo url_for('disponibilidad')
en los templates se actualizo a url_for('disponibilidad.disponibilidad').
"""
from flask import Blueprint, render_template, request

import db

bp = Blueprint("disponibilidad", __name__)


@bp.route("/disponibilidad")
def disponibilidad():
    """Ranking de los equipos con peor disponibilidad -- para encontrar el
    que anda fallando seguido (varias caidas cortas) y no solo el que esta
    caido ahora mismo, que ya se ve de entrada en el inventario."""
    dias = request.args.get("dias", 30, type=int)
    if dias not in (7, 30, 90):
        dias = 30
    orden = request.args.get("orden", "disponibilidad")
    if orden not in ("disponibilidad", "caidas", "dias", "ip"):
        orden = "disponibilidad"
    ranking = db.ranking_disponibilidad(dias=dias, limite=25, orden=orden)
    return render_template("disponibilidad.html", ranking=ranking, dias=dias, orden=orden)
