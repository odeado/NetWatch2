"""Win NetWatch RMM - Puente de avisos hacia la pantalla chica (1024x600).

Este PC tiene una segunda pantalla fija de 1024x600 donde corren otros
paneles (AdminSensor, ups_monitor, MiCasaSmart -- ver C:\\Scripts\\).
Cuando Andres no tiene la pestana de NetWatch al frente, se pierde los
toasts de "equipo caido/volvio online" porque esos solo viven dentro de esa
pagina web. Este script corre aparte (sin depender del navegador), consulta
el mismo /api/estado que ya usa la pagina, y dibuja una ventana flotante
"siempre encima" con los mismos avisos sobre esa segunda pantalla -- pero
solo cuando la ventana activa en este momento NO es la de NetWatch (si ya la
esta mirando, el toast de la propia pagina alcanza).

No hace falta saber nada de los otros 3 sistemas: esta ventana flota encima
de lo que sea que este mostrando esa pantalla en ese momento.
"""

import ctypes
import json
import threading
import time
import tkinter as tk
import urllib.request
import winsound
from ctypes import wintypes
from pathlib import Path

NETWATCH_API_URL = "http://localhost:5001/api/estado"
INTERVALO_POLL_MS = 5000
DURACION_TOAST_MS = 6000
ARCHIVO_ESTADO = Path(__file__).parent / "toast_pantalla1_estado.json"
SONIDO_ACTIVO = True  # esta pantalla no se mira directo, el sonido es lo que hace notar el aviso

COLOR_FONDO = "#171e2e"
COLOR_BORDE = "#262f45"
COLOR_TEXTO = "#e6e9f0"
COLOR_MUTED = "#8a93a8"
COLORES_TIPO = {"online": "#34d399", "offline": "#f87171", "nuevo": "#60a5fa"}
ETIQUETAS_TIPO = {"online": "Volvio online", "offline": "Paso a offline", "nuevo": "Nuevo equipo detectado"}

ANCHO_TOAST = 300
ALTO_TOAST = 64
MARGEN = 12

user32 = ctypes.windll.user32


def _detectar_pantalla_1024x600():
    """Misma logica que usa AdminSensor (main.js) para encontrar su monitor:
    busca uno con resolucion exacta 1024x600 y si no existe, usa el mas
    chico de los conectados. Asi el toast siempre cae en la misma pantalla
    fija sin importar en que PC/monitor N este conectada."""
    monitores = []
    MonitorEnumProc = ctypes.WINFUNCTYPE(
        ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
    )

    def _callback(hmonitor, hdc, rect, data):
        r = rect.contents
        monitores.append((r.left, r.top, r.right - r.left, r.bottom - r.top))
        return 1

    user32.EnumDisplayMonitors(0, 0, MonitorEnumProc(_callback), 0)

    for (x, y, w, h) in monitores:
        if w == 1024 and h == 600:
            return x, y, w, h
    if monitores:
        return min(monitores, key=lambda m: m[2] * m[3])
    return 0, 0, 1024, 600


def _reproducir_sonido(tipo):
    """Winsound.Beep bloquea el hilo que lo llama mientras suena, por eso va
    en un hilo aparte -- si no, se congelaria el mainloop de Tkinter (y con
    el toda la ventana) durante el pitido. Mismos tonos que ya usa el toast
    de la pagina web (netwatchReproducirSonido en index.html), asi el aviso
    suena igual sin importar en que pantalla lo escuches."""
    if not SONIDO_ACTIVO:
        return

    def _tocar():
        try:
            if tipo == "offline":
                for _ in range(2):
                    winsound.Beep(440, 150)
                    winsound.Beep(300, 200)
            elif tipo == "online":
                winsound.Beep(660, 150)
                winsound.Beep(880, 150)
            else:
                winsound.Beep(520, 150)
                winsound.Beep(520, 150)
        except RuntimeError:
            pass  # sin altavoz disponible en este PC

    threading.Thread(target=_tocar, daemon=True).start()


def _titulo_ventana_activa():
    hwnd = user32.GetForegroundWindow()
    largo = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(largo + 1)
    user32.GetWindowTextW(hwnd, buf, largo + 1)
    return buf.value or ""


def _mirando_netwatch():
    return "netwatch" in _titulo_ventana_activa().lower()


def _cargar_ultimo_id():
    try:
        return json.loads(ARCHIVO_ESTADO.read_text(encoding="utf-8")).get("ultimo_id", 0)
    except (OSError, ValueError):
        return 0


def _guardar_ultimo_id(ultimo_id):
    try:
        ARCHIVO_ESTADO.write_text(json.dumps({"ultimo_id": ultimo_id}), encoding="utf-8")
    except OSError:
        pass


class PuenteToasts:
    def __init__(self):
        self.mon_x, self.mon_y, self.mon_w, self.mon_h = _detectar_pantalla_1024x600()

        self.root = tk.Tk()
        self.root.withdraw()  # ventana raiz invisible, solo hace de mainloop

        self.activos = []  # lista de dicts: {"win", "equipo_id", "sticky"}
        self.ultimo_id = _cargar_ultimo_id()
        self.equipos_por_id = {}

        self._poll()
        self.root.mainloop()

    # --- red -----------------------------------------------------------
    def _poll(self):
        try:
            with urllib.request.urlopen(NETWATCH_API_URL, timeout=4) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            self.equipos_por_id = {e["id"]: e for e in data.get("equipos", [])}
            eventos = sorted(data.get("eventos", []), key=lambda e: e["id"])

            if not eventos:
                pass
            elif self.ultimo_id == 0 and not ARCHIVO_ESTADO.exists():
                # primera corrida: no mostrar de golpe todo el historial reciente
                self.ultimo_id = max(e["id"] for e in eventos)
                _guardar_ultimo_id(self.ultimo_id)
            else:
                nuevos = [e for e in eventos if e["id"] > self.ultimo_id]
                if nuevos and not _mirando_netwatch():
                    for ev in nuevos:
                        self._mostrar_toast(ev)
                if nuevos:
                    self.ultimo_id = max(e["id"] for e in nuevos)
                    _guardar_ultimo_id(self.ultimo_id)
        except Exception:
            pass  # web caida/reiniciando o sin red -- se reintenta en el proximo poll
        finally:
            self.root.after(INTERVALO_POLL_MS, self._poll)

    # --- toasts ----------------------------------------------------------
    def _mostrar_toast(self, ev):
        tipo = ev.get("tipo", "nuevo")
        equipo_id = ev.get("equipo_id")
        equipo = self.equipos_por_id.get(equipo_id) if equipo_id else None
        es_critico_offline = tipo == "offline" and bool(equipo and equipo.get("critico"))

        if tipo == "online" and equipo_id:
            self._cerrar_por_equipo(equipo_id)

        titulo = (equipo.get("responsable") if equipo else None) or ev.get("hostname") or ev.get("ip") or "?"
        etiqueta = ETIQUETAS_TIPO.get(tipo, tipo)
        color = COLORES_TIPO.get(tipo, COLOR_MUTED)
        _reproducir_sonido(tipo)

        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        try:
            win.attributes("-alpha", 0.96)
        except tk.TclError:
            pass
        win.configure(bg=COLOR_BORDE)

        marco = tk.Frame(win, bg=COLOR_FONDO, padx=10, pady=8)
        marco.pack(fill="both", expand=True, padx=(4, 0))  # deja ver el borde de color a la izquierda
        borde = tk.Frame(win, bg=color, width=4)
        borde.place(x=0, y=0, relheight=1)

        if es_critico_offline:
            tk.Label(marco, text="CRITICO", bg=COLOR_FONDO, fg=color, font=("Segoe UI", 8, "bold")).pack(anchor="w")
        tk.Label(marco, text=titulo, bg=COLOR_FONDO, fg=COLOR_TEXTO, font=("Segoe UI", 10, "bold"), anchor="w").pack(fill="x")
        sub = etiqueta + "  \u00b7  " + (ev.get("ip") or "")
        tk.Label(marco, text=sub, bg=COLOR_FONDO, fg=COLOR_MUTED, font=("Segoe UI", 9), anchor="w").pack(fill="x")

        cerrar = tk.Label(marco, text="\u00d7", bg=COLOR_FONDO, fg=COLOR_MUTED, font=("Segoe UI", 11, "bold"), cursor="hand2")
        cerrar.place(relx=1.0, y=0, anchor="ne")

        entrada = {"win": win, "equipo_id": equipo_id, "sticky": es_critico_offline}
        self.activos.append(entrada)
        self._reflow()

        def _cerrar(_evt=None):
            self._cerrar_entrada(entrada)

        cerrar.bind("<Button-1>", _cerrar)
        for widget in (win, marco):
            widget.bind("<Button-1>", _cerrar)

        if not es_critico_offline:
            self.root.after(DURACION_TOAST_MS, _cerrar)

    def _cerrar_entrada(self, entrada):
        if entrada not in self.activos:
            return
        self.activos.remove(entrada)
        try:
            entrada["win"].destroy()
        except tk.TclError:
            pass
        self._reflow()

    def _cerrar_por_equipo(self, equipo_id):
        for entrada in [e for e in self.activos if e["equipo_id"] == equipo_id and e["sticky"]]:
            self._cerrar_entrada(entrada)

    def _reflow(self):
        x = self.mon_x + self.mon_w - ANCHO_TOAST - MARGEN
        y = self.mon_y + MARGEN
        for entrada in self.activos:
            win = entrada["win"]
            win.geometry(f"{ANCHO_TOAST}x{ALTO_TOAST}+{x}+{y}")
            y += ALTO_TOAST + 8


if __name__ == "__main__":
    PuenteToasts()
