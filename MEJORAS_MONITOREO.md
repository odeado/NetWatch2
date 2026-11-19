# Mejoras del monitoreo — 15 de octubre de 2026

## Estado de los colectores

La franja de ciudades usa la fecha real de `generated_at` y el intervalo de
consulta de cada sitio. Activo: hasta 1,5 intervalos (mínimo 60 segundos).
Retrasado: hasta 3 intervalos (mínimo 10 minutos). Después queda sin datos.
Los relojes adelantados más de dos minutos no se consideran observaciones válidas.
Si el monitor central deja de consultar, la interfaz también envejece el estado.

Un colector sin datos deja sus equipos como **Sin información reciente** y
conserva el último estado conocido. No suma fallos ni dispara avisos de caída.
Las redes locales 172.30.100.0/24 y 172.30.101.0/24 conservan el escaneo directo
de respaldo. Una observación real sin respuestas sí puede confirmar una caída.

## Observaciones y disponibilidad

El cursor del último escaneo remoto se guarda en SQLite junto con los cambios
de equipos e infraestructura, en una transacción. El mismo reporte no vuelve a
sumar fallos después de reiniciar. Los reportes anteriores tampoco retroceden
el cursor. Se aceptan las fechas antiguas sin zona horaria; las nuevas cargas
de firebase_push incluyen la zona horaria.

La disponibilidad excluye los tramos sin información y muestra su cobertura.
Los equipos descartados se excluyen del total operativo, la salud por red,
el ranking y las notificaciones de conectividad. Sus fichas siguen accesibles
desde el filtro explícito de descartados.

## Avisos

Tres o más cambios del mismo tipo y subred en una ventana de dos minutos se
agrupan. Se conserva cada evento original y se resaltan los equipos críticos.
La web y el puente de escritorio comparten esta regla. El puente conserva
la pantalla secundaria fija de 1024×600 y su comportamiento sobre otras ventanas.
La consulta incremental permite recuperar más de los últimos 30 eventos.

## Posibles cambios de IP

Una detección pendiente puede sugerir una coincidencia con una ficha confirmada
por MAC, hostname o número de serie. Los valores genéricos se descartan.
La propuesta aparece en el inventario y en el panel de edición. No se fusiona
sin una confirmación explícita. También se puede ignorar una propuesta.

La fusión conserva el identificador de la ficha conocida y sus datos completos,
completa campos vacíos, adopta la conectividad de la nueva IP y transfiere
historial, tickets y conexiones RDP. Se revalida la coincidencia dentro de
la transacción antes de modificar las fichas.

Los cambios de Python requieren reiniciar la web, el monitor y el puente.
Después, una recarga con Ctrl+F5 actualiza los recursos del navegador.
