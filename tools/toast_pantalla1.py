"""Win NetWatch RMM - Puente de avisos hacia la pantalla chica (1024x600).

Este PC tiene una segunda pantalla fija de 1024x600 donde corren otros
paneles (AdminSensor, ups_monitor, MiCasaSmart -- ver C:\\Scripts\\).
Cuando Andres no tiene la pestana de NetWatch al frente, se pierde los
toasts de "equipo caido/volvio online" porque esos solo viven dentro de esa
pagina web. Este script corre aparte (sin depender del navegador), consulta
el mismo /api/estado que ya usa la pagina, y dibuja una ventana flotante
"siempre encima" con los mismos avisos sobre esa segunda pantalla,
incluso con el navegador minimizado. Los avisos nativos se muestran siempre;
si NetWatch esta al frente, se evita duplicar el sonido de la pagina web.

No hace falta saber nada de los otros 3 sistemas: esta ventana flota encima
de lo que sea que este mostrando esa pantalla en ese momento.
"""

import ctypes
import json
import logging
from logging.handlers import RotatingFileHandler
import sys
import threading
import time
import tkinter as tk
import urllib.request
import winsound
from ctypes import wintypes
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'webapp'))
from notificaciones import agrupar_eventos

NETWATCH_API_URL = "http://localhost:5001/api/estado"
INTERVALO_POLL_MS = 5000
DURACION_TOAST_MS = 6000
ARCHIVO_ESTADO = Path(__file__).parent / "toast_pantalla1_estado.json"
SONIDO_ACTIVO = True  # esta pantalla no se mira directo, el sonido es lo que hace notar el aviso

COLOR_FONDO = "#151e2e"
COLOR_BORDE = "#293548"
COLOR_TEXTO = "#e7edf7"
COLOR_MUTED = "#a1aec3"
COLORES_TIPO = {"online": "#4adea3", "offline": "#f48891", "nuevo": "#60a5fa"}
ETIQUETAS_TIPO = {"online": "Equipo conectado", "offline": "Equipo desconectado", "nuevo": "Nuevo equipo detectado"}

ANCHO_TOAST = 340
ALTO_TOAST = 92
MARGEN = 12

user32 = ctypes.windll.user32
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetWindowPos.restype = wintypes.BOOL
logger = logging.getLogger("netwatch.avisos")


def _seleccionar_pantalla(monitores):
    for monitor in monitores:
        if monitor[2:] == (1024, 600):
            return monitor
    return min(monitores, key=lambda m: m[2] * m[3]) if monitores else (0, 0, 1024, 600)


def _posiciones_visibles(pantalla, cantidad):
    """Coordenadas absolutas: admite pantallas a la izquierda o arriba."""
    x, y, ancho, alto = pantalla
    capacidad = max(1, (alto - 2 * MARGEN + 8) // (ALTO_TOAST + 8))
    return [(x + ancho - ANCHO_TOAST - MARGEN, y + MARGEN + i * (ALTO_TOAST + 8))
            for i in range(min(cantidad, capacidad))]


def _configurar_log():
    handler = RotatingFileHandler(Path(__file__).with_suffix(".log"), maxBytes=500_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


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

    return _seleccionar_pantalla(monitores)


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
    def __init__(self, prueba=False):
        # La geometria de Tk y las coordenadas de Windows deben usar la
        # misma escala, tambien si cada monitor tiene un DPI distinto.
        try:
            user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except AttributeError:
            user32.SetProcessDPIAware()
        self.mon_x, self.mon_y, self.mon_w, self.mon_h = _detectar_pantalla_1024x600()
        logger.info("Pantalla de avisos: x=%s y=%s ancho=%s alto=%s", self.mon_x, self.mon_y, self.mon_w, self.mon_h)

        self.root = tk.Tk()
        self.root.withdraw()  # ventana raiz invisible, solo hace de mainloop
        self.root.report_callback_exception = lambda tipo, valor, traza: logger.error(
            "Error en una ventana de aviso", exc_info=(tipo, valor, traza))

        self.activos = []  # lista de dicts: {"win", "equipo_id", "sticky"}
        self.ultimo_id = _cargar_ultimo_id()
        self.equipos_por_id = {}

        self.root.after(1000, self._mantener_encima)
        if prueba:
            self.root.after(800, lambda: self._mostrar_toast({"tipo": "nuevo", "prueba": True,
                "hostname": "NetWatch · Avisos de equipos activos", "ip": "Visible aunque el navegador esté minimizado"}))
        self._poll()
        self.root.mainloop()

    # --- red -----------------------------------------------------------
    def _poll(self):
        try:
            url = NETWATCH_API_URL + ('?despues=' + str(self.ultimo_id) if self.ultimo_id else '')
            with urllib.request.urlopen(url, timeout=4) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            self.equipos_por_id = {e["id"]: e for e in data.get("equipos", [])}
            for entrada in list(self.activos):
                ids = entrada.get('equipo_ids') or []
                if ids and entrada['sticky'] and all(
                    i not in self.equipos_por_id or self.equipos_por_id.get(i,{}).get('en_linea') or self.equipos_por_id.get(i,{}).get('sin_datos')
                    or self.equipos_por_id.get(i,{}).get('estado_deteccion')=='descartado' for i in ids):
                    self._cerrar_entrada(entrada)
            eventos = sorted(data.get("eventos", []), key=lambda e: e["id"])

            if not eventos:
                pass
            elif self.ultimo_id == 0 and not ARCHIVO_ESTADO.exists():
                # primera corrida: no mostrar de golpe todo el historial reciente
                self.ultimo_id = max(e["id"] for e in eventos)
                _guardar_ultimo_id(self.ultimo_id)
            else:
                nuevos = [e for e in eventos if e["id"] > self.ultimo_id]
                if nuevos:
                    for ev in agrupar_eventos(nuevos):
                        self._mostrar_toast(ev)
                if nuevos:
                    self.ultimo_id = max(e["id"] for e in nuevos)
                    _guardar_ultimo_id(self.ultimo_id)
        except Exception:
            logger.exception("No se pudo consultar o mostrar un aviso; se reintentara")
        finally:
            self.root.after(INTERVALO_POLL_MS, self._poll)

    # --- toasts ----------------------------------------------------------
    def _mostrar_toast(self, ev):
        tipo = ev.get("tipo", "nuevo")
        equipo_id = ev.get("equipo_id")
        equipo = self.equipos_por_id.get(equipo_id) if equipo_id else None
        es_critico_offline = tipo == "offline" and (bool(equipo and equipo.get("critico")) or bool(ev.get('criticos')))

        if tipo == "online" and equipo_id:
            self._cerrar_por_equipo(equipo_id)

        titulo = (equipo.get("responsable") if equipo else None) or ev.get("hostname") or ev.get("ip") or "?"
        etiqueta = "Prueba de notificación" if ev.get("prueba") else ETIQUETAS_TIPO.get(tipo, tipo)
        color = COLORES_TIPO.get(tipo, COLOR_MUTED)
        if ev.get("prueba") or not _mirando_netwatch():
            _reproducir_sonido(tipo)

        win = tk.Toplevel(self.root)
        win.withdraw()
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        try:
            win.attributes("-alpha", 0.96)
        except tk.TclError:
            pass
        win.configure(bg=COLOR_BORDE)

        marco = tk.Frame(win, bg=COLOR_FONDO, padx=12, pady=10)
        marco.pack(fill="both", expand=True, padx=(3, 1), pady=1)
        borde = tk.Frame(win, bg=color, width=3)
        borde.place(x=0, y=0, relheight=1)

        icono = tk.Canvas(marco, width=30, height=30, bg=COLOR_FONDO, highlightthickness=0)
        icono.pack(side="left", anchor="n", padx=(0, 10), pady=(3, 0))
        if es_critico_offline:
            icono.create_polygon(15, 3, 28, 26, 2, 26, fill="", outline=color, width=2)
            icono.create_line(15, 10, 15, 17, fill=color, width=2)
            icono.create_oval(14, 21, 16, 23, fill=color, outline=color)
        elif tipo == "online":
            icono.create_line(1, 15, 8, 15, 12, 5, 18, 25, 22, 15, 29, 15, fill=color, width=2)
        else:
            icono.create_rectangle(3, 4, 27, 21, outline=color, width=2)
            icono.create_line(15, 21, 15, 27, 9, 27, 21, 27, fill=color, width=2)
        texto = tk.Frame(marco, bg=COLOR_FONDO)
        texto.pack(side="left", fill="both", expand=True, padx=(0, 18))
        if es_critico_offline:
            etiqueta += " · Crítico"
        tk.Label(texto, text=etiqueta, bg=COLOR_FONDO, fg=color, font=("Segoe UI", 8, "bold"), anchor="w").pack(fill="x")
        tk.Label(texto, text=titulo, bg=COLOR_FONDO, fg=COLOR_TEXTO, font=("Segoe UI", 10, "bold"), anchor="w").pack(fill="x")
        tk.Label(texto, text=ev.get('detalle') or ev.get("ip") or "", bg=COLOR_FONDO, fg=COLOR_MUTED, font=("Segoe UI", 8), anchor="w").pack(fill="x")

        cerrar = tk.Label(marco, text="\u00d7", bg=COLOR_FONDO, fg=COLOR_MUTED, font=("Segoe UI", 11, "bold"), cursor="hand2")
        cerrar.place(relx=1.0, y=0, anchor="ne")

        win.update_idletasks()
        hwnd = user32.GetAncestor(win.winfo_id(), 2) or win.winfo_id()  # GA_ROOT
        estilo = user32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
        user32.SetWindowLongW(hwnd, -20, estilo | 0x08000000 | 0x00000080)  # NOACTIVATE | TOOLWINDOW
        entrada = {"win": win, "hwnd": hwnd, "equipo_id": equipo_id, "sticky": es_critico_offline,
                   "equipo_ids": ev.get('equipo_ids') or []}
        self.activos.append(entrada)
        self._reflow()
        logger.info("Aviso mostrado: tipo=%s prueba=%s", tipo, bool(ev.get("prueba")))
        if ev.get("prueba"):
            logger.info("Prueba de escritorio posicionada en x=%s y=%s, siempre encima y sin activar la ventana", *entrada["posicion"])

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
        posiciones = _posiciones_visibles((self.mon_x, self.mon_y, self.mon_w, self.mon_h), len(self.activos))
        visibles = list(reversed(self.activos))[:len(posiciones)]
        for entrada in self.activos:
            if entrada not in visibles:
                entrada["win"].withdraw()
        for entrada, (x, y) in zip(visibles, posiciones):
            entrada["posicion"] = (x, y)
            entrada["win"].geometry(f"{ANCHO_TOAST}x{ALTO_TOAST}")
            entrada["win"].deiconify()
            self._posicionar_encima(entrada)

    def _posicionar_encima(self, entrada):
        x, y = entrada["posicion"]
        # Coordenadas absolutas y SWP_NOACTIVATE: funciona en monitores con
        # origen negativo sin robar el foco ni restaurar el navegador.
        if not user32.SetWindowPos(entrada["hwnd"], -1, x, y, ANCHO_TOAST, ALTO_TOAST, 0x0010 | 0x0040):
            raise ctypes.WinError()

    def _mantener_encima(self):
        try:
            for entrada in self.activos:
                if entrada["win"].state() != "withdrawn":
                    self._posicionar_encima(entrada)
        except Exception:
            logger.exception("No se pudo mantener un aviso encima")
        finally:
            self.root.after(1000, self._mantener_encima)


if __name__ == "__main__":
    _configurar_log()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    mutex = kernel32.CreateMutexW(None, False, "Local\\NetWatchDesktopToasts")
    if mutex and ctypes.get_last_error() != 183:
        try:
            PuenteToasts(prueba="--prueba" in sys.argv)
        except Exception:
            logger.exception("No se pudo iniciar el puente de avisos")
        finally:
            kernel32.CloseHandle(mutex)
    elif mutex:
        kernel32.CloseHandle(mutex)
