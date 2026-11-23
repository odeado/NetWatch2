"""Pruebas del colector con un Firebase falso en memoria.
Correr:  python -m unittest test_colector -v
"""
import copy
import unittest

import colector_netwatch as c


class FirebaseFalso:
    def __init__(self):
        self.arbol = {}
        self.caido = False
        self.n_post = 0

    def _nodo(self, ruta, crear=False):
        n = self.arbol
        for p in [x for x in ruta.split("/") if x]:
            if not isinstance(n, dict) or p not in n:
                if not crear:
                    return None
                n[p] = {}
            n = n[p]
        return n

    def get(self, ruta, **q):
        if self.caido:
            raise c.FirebaseError("conexion: caido")
        n = self._nodo(ruta)
        if q.get("shallow") and isinstance(n, dict):
            return {k: True for k in n}
        return copy.deepcopy(n)

    def _escribir(self, ruta, valor):
        partes = [x for x in ruta.split("/") if x]
        padre = self._nodo("/".join(partes[:-1]), crear=True)
        if valor is None:
            padre.pop(partes[-1], None)
        else:
            padre[partes[-1]] = copy.deepcopy(valor)

    def put(self, ruta, cuerpo):
        if self.caido:
            raise c.FirebaseError("conexion: caido")
        self._escribir(ruta, cuerpo)

    def post(self, ruta, cuerpo):
        if self.caido:
            raise c.FirebaseError("conexion: caido")
        self.n_post += 1
        self._escribir("%s/-N%06d" % (ruta, self.n_post), cuerpo)

    def patch(self, ruta, cuerpo):
        for k, v in cuerpo.items():
            self._escribir("%s/%s" % (ruta, k), v)

    # helpers de prueba
    def escaneo(self, sitio, generado, vivos, muertos=()):
        hosts = [{"ip": ip, "alive": True, "hostname": "PC-" + ip.split(".")[-1]} for ip in vivos]
        hosts += [{"ip": ip, "alive": False} for ip in muertos]
        self.put("escaneos_remotos/" + sitio, {"generated_at": generado, "cidr": "x", "hosts": hosts})

    def eventos(self):
        return [v for _, v in sorted((self.arbol.get("colector", {}).get("eventos") or {}).items())]


def tipos(evs):
    return [(e["tipo"], e.get("ip")) for e in evs]


class PruebasColector(unittest.TestCase):
    def setUp(self):
        self.fb = FirebaseFalso()
        self.t = [1000.0]
        cfg = dict(c.POR_DEFECTO, criticos_manuales=["10.0.0.2"])
        self.estado = {}
        self.col = c.Colector(self.fb, cfg, self.estado, reloj=lambda: self.t[0])

    def avanzar(self, seg):
        self.t[0] += seg

    def test_primer_escaneo_es_silencioso(self):
        self.fb.escaneo("rendic", "g1", ["10.0.0.1", "10.0.0.2"], ["10.0.0.3"])
        self.assertEqual(self.col.ciclo(), [])
        self.assertEqual(self.fb.eventos(), [])
        self.assertEqual(self.fb.arbol["colector"]["estado"]["sitios"]["rendic"]["activos"], 2)

    def test_cae_tras_dos_escaneos_vuelve_y_critico(self):
        self.fb.escaneo("rendic", "g1", ["10.0.0.1", "10.0.0.2"])
        self.col.ciclo()
        self.avanzar(120)
        self.fb.escaneo("rendic", "g2", [], ["10.0.0.1", "10.0.0.2"])  # 1 fallo: aun no
        self.assertEqual(self.col.ciclo(), [])
        self.avanzar(120)
        self.fb.escaneo("rendic", "g3", [], ["10.0.0.1", "10.0.0.2"])  # 2 fallos: caen los dos
        evs = self.col.ciclo()
        self.assertEqual(sorted(tipos(evs)), [("cayo", "10.0.0.1"), ("cayo", "10.0.0.2")])
        self.assertEqual({e["ip"]: e["critico"] for e in evs}, {"10.0.0.1": False, "10.0.0.2": True})
        self.avanzar(300)
        self.fb.escaneo("rendic", "g4", ["10.0.0.1"], ["10.0.0.2"])
        evs = self.col.ciclo()
        self.assertEqual(tipos(evs), [("volvio", "10.0.0.1")])
        self.assertEqual(evs[0]["offline_seg"], 300)
        # estado publicado
        st = self.fb.arbol["colector"]
        self.assertEqual(st["estado"]["criticos_offline"], 1)
        self.assertEqual(st["criticos"]["10_0_0_2"]["online"], False)
        self.assertEqual(len(self.fb.eventos()), 3)

    def test_sin_escaneo_nuevo_no_cuenta_fallos(self):
        self.fb.escaneo("rendic", "g1", ["10.0.0.1"])
        self.col.ciclo()
        self.fb.escaneo("rendic", "g2", [], ["10.0.0.1"])
        self.col.ciclo()
        for _ in range(5):  # mismo generated_at: el colector cicla mas rapido que el sitio
            self.avanzar(30)
            self.assertEqual(self.col.ciclo(), [])
        self.assertTrue(self.estado["equipos"]["10.0.0.1"]["online"])

    def test_equipo_nuevo(self):
        self.fb.escaneo("rendic", "g1", ["10.0.0.1"])
        self.col.ciclo()
        self.fb.escaneo("rendic", "g2", ["10.0.0.1", "10.0.0.9"])
        self.assertEqual(tipos(self.col.ciclo()), [("nuevo", "10.0.0.9")])

    def test_sitio_sin_datos_y_recuperado(self):
        self.fb.escaneo("matta", "g1", ["10.1.0.1"])
        self.col.ciclo()
        self.avanzar(11 * 60)
        evs = self.col.ciclo()
        self.assertEqual([(e["tipo"], e["sitio"]) for e in evs], [("sitio_sin_datos", "matta")])
        self.avanzar(60)
        self.assertEqual(self.col.ciclo(), [])  # no repite el aviso
        self.assertTrue(self.fb.arbol["colector"]["estado"]["sitios"]["matta"]["sin_datos"])
        self.fb.escaneo("matta", "g2", ["10.1.0.1"])
        self.assertEqual([e["tipo"] for e in self.col.ciclo()], ["sitio_recuperado"])

    def test_criticos_desde_netwatch(self):
        self.fb.put("equipos", {"a": {"ip": "10.0.0.5", "hostname": "SRV", "critico": 1},
                                "b": {"ip": "10.0.0.6", "critico": 0}})
        self.fb.escaneo("rendic", "g1", ["10.0.0.5", "10.0.0.6"])
        self.col.ciclo()
        self.assertEqual(self.col._todos_los_criticos(), ["10.0.0.2", "10.0.0.5"])
        self.assertIsNone(self.fb.arbol["colector"]["criticos"]["10_0_0_2"]["online"])  # critico manual nunca visto

    def test_firebase_caido_deja_eventos_en_cola(self):
        self.fb.escaneo("rendic", "g1", ["10.0.0.1"])
        self.col.ciclo()
        self.avanzar(120)
        self.fb.escaneo("rendic", "g2", [], ["10.0.0.1"])
        self.col.ciclo()
        self.avanzar(120)
        self.fb.escaneo("rendic", "g3", [], ["10.0.0.1"])
        self.fb.caido = True
        self.col.ciclo()  # no puede leer ni publicar
        self.fb.caido = False
        self.col.ciclo()
        self.assertEqual(self.estado["pendientes"], [])

    def test_poda(self):
        self.col.cfg["max_eventos"] = 3
        for i in range(8):
            self.fb.post("colector/eventos", {"n": i})
        self.fb.escaneo("rendic", "g1", ["10.0.0.1"])
        self.avanzar(4000)
        self.col.ciclo()
        self.assertEqual([e["n"] for e in self.fb.eventos()], [5, 6, 7])


if __name__ == "__main__":
    unittest.main()
