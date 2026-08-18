"""Win NetWatch RMM - Capa de datos (SQLite)
==========================================
Base de datos local del inventario de equipos: estado en linea/fuera de
linea, eventos (online/offline/nuevo), ficha tecnica/administrativa,
tickets de soporte por equipo, e historial de conexiones RDP.

100% libreria estandar (sqlite3 viene con Python).

Este era un solo archivo db.py de 2233 lineas; ahora es un paquete
desglosado por dominio (mismo patron que webapp/blueprints/ para las rutas
de Flask). Cada submodulo se puede leer solo, sin cargar el resto:

    _core.py         conexion SQLite, esquema, utilidades chicas compartidas
    equipos.py        escaneo/estado online-offline, ficha tecnica/admin
    usuarios.py        directorio de responsables (empleados)
    tickets.py          tickets de soporte por equipo
    rdp.py               historial de conexiones RDP
    catalogos.py          departamentos y ciudades
    disponibilidad.py       eventos recientes, % disponibilidad, alertas
    dispositivos.py           topologia de red (switches/routers/puertos)
    importadores.py            imports masivos que cruzan varios dominios

Todo lo publico de cada submodulo se re-exporta aca para que el resto del
codigo (app.py, blueprints/*, firebase_sync.py, scanner/monitor.py) siga
escribiendo exactamente lo mismo que antes: `import db; db.list_equipos()`,
sin que le importe en que archivo interno vive cada funcion.

OJO con tests/monkeypatching: DB_PATH vive en _core.py, no aca -- si algo
necesita apuntar a una base temporal (ver webapp/test_db.py), hay que
reasignar `db._core.DB_PATH`, no `db.DB_PATH` (esa segunda opcion solo
pisaria la copia de este __init__, no la que get_connection() realmente
lee)."""

from . import _core  # noqa: F401  (submodulo expuesto para tests/monkeypatching, ver arriba)
from ._core import DB_PATH, get_connection, init_db  # noqa: F401

from .equipos import (  # noqa: F401
    CATEGORIAS_DISPOSITIVO_RED, CATEGORIAS_EQUIPO, FICHA_FIELDS,
    aplicar_reporte_agente, apply_scan_results, create_equipo_manual,
    delete_equipos, fusionar_equipo_por_ip, get_equipo, get_equipo_by_ip,
    get_equipos_count_por_responsable, import_scan, list_equipos,
    list_equipos_basico, list_equipos_export, list_equipos_por_dispositivo,
    list_equipos_por_responsable, list_scan_files, migrate_legacy_confirmations,
    set_critico, set_responsable_equipo, update_estado, update_ficha,
)

from .usuarios import (  # noqa: F401
    create_usuario, delete_usuario, delete_usuarios,
    find_or_create_usuario_por_nombre, get_usuario, importar_empleados_masivo,
    list_usuarios, update_usuario, update_usuario_estado,
)

from .tickets import (  # noqa: F401
    count_open_tickets, create_ticket, get_open_ticket_counts, get_ticket,
    list_all_tickets, list_tickets_for_equipo, update_ticket_estado,
)

from .rdp import list_rdp_history_for_equipo, log_rdp_connection  # noqa: F401

from .catalogos import (  # noqa: F401
    create_ciudad, create_departamento, delete_ciudad, delete_departamento,
    list_ciudades, list_departamentos,
)

from .disponibilidad import (  # noqa: F401
    _calcular_pct_online, _disponibilidad_desde_conn, _ranking_clave_orden,
    calcular_disponibilidad, equipos_criticos_pendientes_alerta,
    list_recent_events, marcar_alerta_offline_enviada, ranking_disponibilidad,
)

from .dispositivos import (  # noqa: F401
    ESTADOS_DISPOSITIVO, PLANTILLAS_PUERTOS, TIPOS_DISPOSITIVO,
    TIPO_DISPOSITIVO_LABELS, _inferir_tipo_y_plantilla, _parsear_bocas,
    aplicar_estado_red, assign_puerto, create_dispositivo, eliminar_dispositivo,
    get_destino_dispositivo_anterior, get_dispositivo, get_puertos_definicion,
    list_conexiones_dispositivos, list_dispositivos, set_puerto_destino,
    update_dispositivo,
)

from .importadores import (  # noqa: F401
    importar_gestion_masiva, importar_infraestructura_masiva,
    importar_inventario_masivo,
)
