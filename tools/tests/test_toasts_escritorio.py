"""Pruebas sin crear ventanas ni consultar equipos reales."""
import importlib.util
from io import BytesIO
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location("avisos", Path(__file__).resolve().parents[1] / "toast_pantalla1.py")
avisos = importlib.util.module_from_spec(spec)
spec.loader.exec_module(avisos)


class AvisosEscritorioTest(unittest.TestCase):
    def test_selects_fixed_secondary_screen(self):
        monitores = [(0, 0, 1920, 1080), (-1024, -120, 1024, 600)]
        self.assertEqual(avisos._seleccionar_pantalla(monitores), monitores[1])

    def test_negative_coordinates_stay_on_secondary_screen(self):
        posiciones = avisos._posiciones_visibles((-1024, -600, 1024, 600), 15)
        self.assertEqual(len(posiciones), 5)
        for x, y in posiciones:
            self.assertGreaterEqual(x, -1024)
            self.assertGreaterEqual(y, -600)
            self.assertLessEqual(x + avisos.ANCHO_TOAST, 0)
            self.assertLessEqual(y + avisos.ALTO_TOAST, 0)

    def test_topmost_uses_absolute_position_without_activation(self):
        puente = avisos.PuenteToasts.__new__(avisos.PuenteToasts)
        with patch.object(avisos.user32, "SetWindowPos", return_value=True) as posicionar:
            puente._posicionar_encima({"hwnd": 123, "posicion": (-352, -588)})
        posicionar.assert_called_once_with(123, -1, -352, -588, avisos.ANCHO_TOAST, avisos.ALTO_TOAST, 0x50)

    def test_newest_notifications_remain_inside_screen(self):
        puente = avisos.PuenteToasts.__new__(avisos.PuenteToasts)
        puente.mon_x, puente.mon_y, puente.mon_w, puente.mon_h = (-1024, 0, 1024, 600)
        puente.activos = [{"win": Mock(), "hwnd": i} for i in range(8)]
        puente._posicionar_encima = Mock()
        puente._reflow()
        self.assertEqual(puente._posicionar_encima.call_count, 5)
        self.assertEqual(puente.activos[-1]["posicion"], (-352, 12))
        puente.activos[0]["win"].withdraw.assert_called_once()
        puente.activos[-1]["win"].deiconify.assert_called_once()

    def test_poll_shows_native_notification_even_with_netwatch_in_front(self):
        puente = avisos.PuenteToasts.__new__(avisos.PuenteToasts)
        puente.root = Mock()
        puente.ultimo_id = 100
        puente.activos = []
        puente._mostrar_toast = Mock()
        evento = {"id": 101, "tipo": "offline", "equipo_id": 1}
        respuesta = BytesIO(json.dumps({"equipos": [], "eventos": [evento]}).encode())
        with patch.object(avisos.urllib.request, "urlopen", return_value=respuesta), \
             patch.object(avisos, "_guardar_ultimo_id"), \
             patch.object(avisos, "_mirando_netwatch", return_value=True):
            puente._poll()
        puente._mostrar_toast.assert_called_once_with(evento)
        self.assertEqual(puente.ultimo_id, 101)
        puente.root.after.assert_called_once_with(avisos.INTERVALO_POLL_MS, puente._poll)


if __name__ == "__main__":
    unittest.main()
