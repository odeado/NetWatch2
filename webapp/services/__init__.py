"""Win NetWatch RMM - Capa de servicios (lógica de negocio)
===========================================================
Orquestación y validación de datos que vive en las rutas de Flask --
Fase 2 del reorden. La idea es que cada ruta quede delgada: recibe el
request, llama a una función de acá, y decide qué responder (redirect/
render/fragmento ajax) según el resultado.

Cada submodulo corresponde a un dominio de negocio:

    equipos.py       Alta/edición/fusión de equipos, vista de admin,
                     filtrado de dispositivos de red por subred.

El acceso a sqlite sigue viviendo SOLO en db/ (ver webapp/db/__init__.py)
-- los servicios no abren conexiones, solo llaman a db.* y validan/arman
los datos antes y después. Esto deja que la base de datos sea un detalle
de implementación: si algún día se migra a PostgreSQL o Firebase, solo
hay que actualizar db/, los servicios no cambian.

Todo lo publico de cada submodulo se puede importar directo:

    from services import equipos as equipos_service
    resultado = equipos_service.actualizar_ficha(equipo_id, request)

O más explícitamente:

    from services.equipos import actualizar_ficha
    resultado = actualizar_ficha(equipo_id, request)
