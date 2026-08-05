"""Prueba puntual del aviso de WhatsApp -- manda un mensaje de un equipo
'offline' inventado usando el mismo codigo real que usa el monitor
(whatsapp_alertas.py + los datos ya guardados en config.json). No toca la
base de datos ni deja nada prendido de forma permanente (el "habilitado"
se fuerza a True solo en memoria, para esta prueba puntual).

Uso: parado en la carpeta scanner/, corre:
    python test_whatsapp.py

Borra este archivo cuando ya no lo necesites, es solo para probar.
"""
import scanner
import whatsapp_alertas

config = scanner.load_config()
config["alertas"]["habilitado"] = True  # solo en memoria, para esta prueba

equipo_prueba = {
    "ip": "172.30.100.50",
    "hostname": "PC-PRUEBA",
    "responsable": None,
    "sucursal": None,
    "ciudad": "Antofagasta (prueba)",
    "minutos_offline": 20,
}

ok = whatsapp_alertas.enviar_alerta_equipo_offline(equipo_prueba, config)
print("Resultado:", "ENVIADO -- revisa tu WhatsApp" if ok else "FALLO -- revisa el detalle de arriba")
