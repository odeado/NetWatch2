"""Tests de la logica mas delicada de db.py -- la que mas facil se rompe sin
darse cuenta al reorganizar el codigo, y la que menos perdon tiene si falla
(gatilla o silencia alertas de WhatsApp de equipos criticos):

- el conteo de fallos_consecutivos antes de marcar un equipo offline
  (ver el comentario en apply_scan_results sobre por que existe)
- el calculo de % de disponibilidad reconstruido desde la tabla eventos
- el criterio de orden de /disponibilidad (ver _ranking_clave_orden)

Corre con (parado en webapp/):
    python test_db.py
o:  python -m unittest test_db -v

100% libreria estandar (unittest), sin pytest -- mismo criterio que el resto
del proyecto (ver el docstring de db.py). No toca netwatch.db: cada test que
necesita base usa un archivo sqlite temporal aparte.
"""
import os
import tempfile
import unittest
from datetime import datetime

import db


class TestCalcularPctOnline(unittest.TestCase):
    """_calcular_pct_online es pura (no toca la base), asi que estos tests
    corren instantaneo y cubren la reconstruccion de transiciones online/
    offline que alimenta tanto la ficha de un equipo como el ranking."""

    def test_sin_eventos_100_por_ciento(self):
        pct, caidas = db._calcular_pct_online([], datetime(2026, 1, 1), datetime(2026, 1, 2))
        self.assertEqual(pct, 100.0)
        self.assertEqual(caidas, 0)

    def test_una_caida_a_mitad_del_periodo(self):
        inicio, fin = datetime(2026, 1, 1, 0, 0), datetime(2026, 1, 2, 0, 0)  # 24h totales
        eventos = [
            (datetime(2026, 1, 1, 12, 0), "offline"),
            (datetime(2026, 1, 1, 18, 0), "online"),
        ]  # 6h offline de 24h = 25% offline
        pct, caidas = db._calcular_pct_online(eventos, inicio, fin)
        self.assertEqual(pct, 75.0)
        self.assertEqual(caidas, 1)

    def test_offline_sin_volver_cuenta_hasta_el_fin(self):
        inicio, fin = datetime(2026, 1, 1, 0, 0), datetime(2026, 1, 2, 0, 0)
        eventos = [(datetime(2026, 1, 1, 18, 0), "offline")]  # se cae y no vuelve en la ventana
        pct, caidas = db._calcular_pct_online(eventos, inicio, fin)
        self.assertEqual(pct, 75.0)
        self.assertEqual(caidas, 1)

    def test_arranca_offline_desde_el_inicio_no_cuenta_como_caida(self):
        inicio, fin = datetime(2026, 1, 1, 0, 0), datetime(2026, 1, 2, 0, 0)
        eventos = [(datetime(2026, 1, 1, 6, 0), "online")]  # ya estaba caido al empezar la ventana
        pct, caidas = db._calcular_pct_online(eventos, inicio, fin, online_al_inicio=False)
        self.assertEqual(pct, 75.0)
        self.assertEqual(caidas, 0)

    def test_eventos_duplicados_seguidos_se_ignoran(self):
        inicio, fin = datetime(2026, 1, 1, 0, 0), datetime(2026, 1, 2, 0, 0)
        eventos = [
            (datetime(2026, 1, 1, 12, 0), "offline"),
            (datetime(2026, 1, 1, 13, 0), "offline"),  # duplicado, no debe contar 2 caidas
            (datetime(2026, 1, 1, 18, 0), "online"),
        ]
        pct, caidas = db._calcular_pct_online(eventos, inicio, fin)
        self.assertEqual(pct, 75.0)
        self.assertEqual(caidas, 1)


class TestRankingClaveOrden(unittest.TestCase):
    """Los 4 criterios de /disponibilidad (ver el filtro agregado en la
    pagina) -- tambien pura, sin base de datos."""

    FILAS = [
        {"ip": "172.30.100.2", "pct_online": 50.0, "caidas": 3, "desde": "2026-07-20T10:00:00"},
        {"ip": "172.30.100.15", "pct_online": 20.0, "caidas": 10, "desde": None},  # online ahora
        {"ip": "172.30.100.1", "pct_online": 80.0, "caidas": 1, "desde": "2026-07-01T10:00:00"},
    ]

    def _orden(self, criterio):
        return [r["ip"] for r in sorted(self.FILAS, key=db._ranking_clave_orden(criterio))]

    def test_disponibilidad_peor_pct_primero(self):
        self.assertEqual(self._orden("disponibilidad"), ["172.30.100.15", "172.30.100.2", "172.30.100.1"])

    def test_caidas_mas_caidas_primero(self):
        self.assertEqual(self._orden("caidas"), ["172.30.100.15", "172.30.100.2", "172.30.100.1"])

    def test_dias_mas_tiempo_offline_primero_y_online_al_final(self):
        self.assertEqual(self._orden("dias"), ["172.30.100.1", "172.30.100.2", "172.30.100.15"])

    def test_ip_orden_numerico_no_alfabetico(self):
        self.assertEqual(self._orden("ip"), ["172.30.100.1", "172.30.100.2", "172.30.100.15"])


class TestApplyScanResultsOfflineAfterMisses(unittest.TestCase):
    """Regression test del bug documentado en apply_scan_results: un solo
    ciclo sin respuesta (hipo de red/firewall) NO debe marcar el equipo
    offline ni generar el evento/alerta -- recien al llegar a
    offline_after_misses fallos SEGUIDOS. Usa un netwatch.db temporal,
    nunca el real."""

    def setUp(self):
        fd, self._tmp_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        # DB_PATH vive en db._core (db.py se desglosao en un paquete) --
        # get_connection() lee la copia de _core, no la de db/__init__, asi
        # que hay que pisar esa.
        self._db_path_original = db._core.DB_PATH
        db._core.DB_PATH = self._tmp_path
        db.init_db()

    def tearDown(self):
        db._core.DB_PATH = self._db_path_original
        os.remove(self._tmp_path)

    @staticmethod
    def _equipo(ip="172.30.100.50", alive=True):
        return {"ip": ip, "alive": alive, "hostname": "PC-PRUEBA"}

    @staticmethod
    def _fila(ip="172.30.100.50"):
        equipo_id = db.get_equipo_by_ip(ip)
        return db.get_equipo(equipo_id) if equipo_id else None

    def test_primer_ciclo_crea_el_equipo_online(self):
        eventos = db.apply_scan_results("172.30.100.0/24", [self._equipo(alive=True)])
        self.assertEqual([e["tipo"] for e in eventos], ["nuevo"])
        self.assertTrue(self._fila()["en_linea"])

    def test_un_solo_fallo_no_marca_offline(self):
        db.apply_scan_results("172.30.100.0/24", [self._equipo(alive=True)])
        eventos = db.apply_scan_results("172.30.100.0/24", [self._equipo(alive=False)], offline_after_misses=2)
        self.assertEqual(eventos, [])  # todavia no llega al umbral: no debe avisar
        fila = self._fila()
        self.assertTrue(fila["en_linea"])  # sigue "online" mientras no llegue al umbral
        self.assertEqual(fila["fallos_consecutivos"], 1)

    def test_llegar_al_umbral_marca_offline_y_genera_un_solo_evento(self):
        db.apply_scan_results("172.30.100.0/24", [self._equipo(alive=True)])
        db.apply_scan_results("172.30.100.0/24", [self._equipo(alive=False)], offline_after_misses=2)
        eventos = db.apply_scan_results("172.30.100.0/24", [self._equipo(alive=False)], offline_after_misses=2)
        self.assertEqual([e["tipo"] for e in eventos], ["offline"])
        self.assertFalse(self._fila()["en_linea"])

    def test_no_repite_el_evento_offline_en_ciclos_siguientes(self):
        db.apply_scan_results("172.30.100.0/24", [self._equipo(alive=True)])
        db.apply_scan_results("172.30.100.0/24", [self._equipo(alive=False)], offline_after_misses=2)
        db.apply_scan_results("172.30.100.0/24", [self._equipo(alive=False)], offline_after_misses=2)
        eventos = db.apply_scan_results("172.30.100.0/24", [self._equipo(alive=False)], offline_after_misses=2)
        self.assertEqual(eventos, [])  # ya estaba offline, no es una transicion nueva

    def test_volver_online_resetea_fallos_y_genera_evento(self):
        db.apply_scan_results("172.30.100.0/24", [self._equipo(alive=True)])
        db.apply_scan_results("172.30.100.0/24", [self._equipo(alive=False)], offline_after_misses=2)
        db.apply_scan_results("172.30.100.0/24", [self._equipo(alive=False)], offline_after_misses=2)
        eventos = db.apply_scan_results("172.30.100.0/24", [self._equipo(alive=True)])
        self.assertEqual([e["tipo"] for e in eventos], ["online"])
        fila = self._fila()
        self.assertTrue(fila["en_linea"])
        self.assertEqual(fila["fallos_consecutivos"], 0)


if __name__ == "__main__":
    unittest.main()
