"""Respalda la base antes de migrar y prepara sugerencias sin fusionarlas."""
from datetime import datetime
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'webapp'))
import db
from db._core import DB_PATH, conexion
from db.coincidencias import detectar_coincidencias


def main():
    if not DB_PATH.is_file():
        raise SystemExit('No existe la base de NetWatch; no se crea una vacía.')
    destino = DB_PATH.with_name('netwatch_backup_antes_monitoreo_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.db')
    with sqlite3.connect(DB_PATH) as origen, sqlite3.connect(destino) as copia:
        origen.backup(copia)
    print('Respaldo:', destino.name)
    db.init_db()
    with conexion() as conn:
        ids = [r[0] for r in conn.execute("SELECT id FROM equipos WHERE estado_deteccion='pendiente'")]
        for equipo_id in ids:
            detectar_coincidencias(conn, equipo_id)
        conn.commit()
    print('Migración completada:', len(ids), 'detecciones revisadas; ninguna fusión automática.')


if __name__ == '__main__':
    main()
