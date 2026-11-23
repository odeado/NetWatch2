"""Puerto de la pagina web de NetWatch.

UNA sola fuente de verdad: el archivo netwatch_puerto.txt en la raiz del
proyecto (una sola linea con el numero). Lo leen app.py (donde escucha la
web), toast_pantalla1.py (que consulta /api/estado) y "Iniciar NetWatch.bat"
(que abre el navegador). Para cambiarlo usa "Cambiar Puerto.bat" (detiene
NetWatch, escribe el archivo y lo vuelve a iniciar) o edita el archivo a mano
y reinicia NetWatch.

Si el archivo no existe o no tiene un numero valido (1024-65535), se usa el
puerto por defecto.
"""
from pathlib import Path

PUERTO_POR_DEFECTO = 6001
ARCHIVO_PUERTO = Path(__file__).resolve().parent.parent / "netwatch_puerto.txt"


def leer_puerto():
    try:
        valor = int(ARCHIVO_PUERTO.read_text(encoding="utf-8-sig").strip())
    except (OSError, ValueError):
        return PUERTO_POR_DEFECTO
    return valor if 1024 <= valor <= 65535 else PUERTO_POR_DEFECTO
