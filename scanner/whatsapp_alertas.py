"""
Win NetWatch RMM - Aviso por WhatsApp para equipos criticos
================================================================
Manda un mensaje de WhatsApp cuando un equipo marcado como "critico" (casilla
en su ficha) lleva offline mas de cierto tiempo. Usa la API oficial de
WhatsApp Cloud (Meta), llamada directo por HTTPS con la libreria estandar
(urllib) -- sin dependencias nuevas, sin Twilio ni libreria de terceros.

Como activarlo
---------------
1. Entra a https://developers.facebook.com/apps y crea una app.
2. Agregale el producto "WhatsApp" (Meta te da un numero de prueba gratis).
3. En el panel de esa app vas a encontrar:
   - Un "Token de acceso temporal" (o genera uno permanente en API System User).
   - El "ID de numero de telefono" (Phone number ID) del numero de prueba.
   - Una seccion para agregar "numeros de telefono de destinatarios de
     prueba" -- mientras uses el token de prueba, cada numero que quieras
     que reciba avisos tiene que estar agregado ahi (con codigo de pais).
4. Completa esos 3 datos en scanner/config.json, dentro de "alertas", y
   cambia "habilitado" a true.

Mientras "habilitado" sea false o falte algun dato, esta funcion no hace
ninguna llamada de red -- solo deja una linea en el log del monitor
avisando que el aviso no se pudo mandar por falta de configuracion.
"""

import json
import urllib.error
import urllib.request


def _config_alertas(config):
    return config.get("alertas", {}) or {}


def alertas_configuradas(config):
    a = _config_alertas(config)
    return bool(
        a.get("habilitado")
        and a.get("whatsapp_token")
        and a.get("whatsapp_phone_number_id")
        and a.get("destinatarios")
    )


def _armar_texto(equipo):
    nombre = equipo.get("responsable") or equipo.get("hostname") or equipo.get("ip")
    ubicacion = " / ".join(p for p in (equipo.get("sucursal"), equipo.get("ciudad")) if p)
    partes = [
        "NetWatch RMM - Equipo critico caido",
        f"{nombre} ({equipo.get('ip')})" + (f" - {ubicacion}" if ubicacion else ""),
        f"Lleva offline {equipo.get('minutos_offline')} minutos.",
    ]
    return "\n".join(partes)


def enviar_alerta_equipo_offline(equipo, config, log=print):
    """Manda el aviso a cada destinatario configurado. Devuelve True si se
    pudo mandar a todos, False si algo fallo o si no esta configurado
    todavia (en ese caso el equipo queda pendiente y se reintenta en el
    proximo ciclo del monitor)."""
    if not alertas_configuradas(config):
        log(
            f"  [ALERTA WHATSAPP OMITIDA] {equipo.get('ip')} es critico y lleva "
            f"{equipo.get('minutos_offline')} min offline, pero el aviso por WhatsApp "
            f"todavia no esta configurado (ver scanner/config.json, seccion 'alertas')."
        )
        return False

    a = _config_alertas(config)
    url = f"https://graph.facebook.com/v20.0/{a['whatsapp_phone_number_id']}/messages"
    texto = _armar_texto(equipo)

    ok_todos = True
    for numero in a["destinatarios"]:
        payload = {
            "messaging_product": "whatsapp",
            "to": numero,
            "type": "text",
            "text": {"body": texto},
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {a['whatsapp_token']}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
            log(f"  [ALERTA WHATSAPP ENVIADA] {equipo.get('ip')} -> {numero}")
        except urllib.error.HTTPError as e:
            detalle = e.read().decode("utf-8", errors="replace")
            log(f"  [ALERTA WHATSAPP FALLO] {equipo.get('ip')} -> {numero}: HTTP {e.code} {detalle}")
            ok_todos = False
        except urllib.error.URLError as e:
            log(f"  [ALERTA WHATSAPP FALLO] {equipo.get('ip')} -> {numero}: {e.reason}")
            ok_todos = False

    return ok_todos
