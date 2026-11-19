"""Regresiones de fotos: todas las escrituras usan SQLite y archivos temporales."""
import base64
import importlib
from io import BytesIO
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from flask import url_for

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import db
from db import _core
from services import equipos

# Importar las rutas no debe inicializar ni migrar la base de produccion.
with patch.object(db, "init_db"):
    app_module = importlib.import_module("app")

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aT1sAAAAASUVORK5CYII=")


class EquipoPhotosTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.patches = [
            patch.object(_core, "DB_PATH", root / "test.db"),
            patch.object(equipos, "UPLOAD_DIR", root / "static" / "uploads"),
            patch.object(app_module.app, "_static_folder", str(root / "static")),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        db.init_db()
        with _core.conexion() as conn:
            self.equipo_id = conn.execute(
                "INSERT INTO equipos (ip, hostname, estado_deteccion, origen) VALUES (?, ?, ?, ?)",
                ("PHOTO-TEST", "PHOTO-TEST", "confirmado", "manual"),
            ).lastrowid
            conn.commit()
        self.client = app_module.app.test_client()

    def save(self, file=None, **fields):
        data = {"ip": "PHOTO-TEST", "hostname": "PHOTO-TEST", **fields}
        if file:
            data["foto_archivo"] = file
        return self.client.post(
            f"/equipo/{self.equipo_id}", data=data,
            headers={"X-Requested-With": "XMLHttpRequest"},
        )

    def upload(self):
        response = self.save((BytesIO(PNG), "equipo.PNG"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("X-Netwatch-Guardado"), "1")
        return db.get_equipo(self.equipo_id)["foto"]

    def test_upload_and_static_image_roundtrip(self):
        photo = self.upload()
        response = self.client.get("/static/" + photo)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "image/png")
        self.assertEqual(response.data, PNG)
        response.close()
        panel = self.client.get(f"/equipo/{self.equipo_id}/panel").get_data(as_text=True)
        self.assertIn("/static/" + photo, panel)

    def test_edit_other_fields_keeps_photo(self):
        photo = self.upload()
        response = self.save(modelo="Modelo actualizado")
        self.assertEqual(response.headers.get("X-Netwatch-Guardado"), "1")
        saved = db.get_equipo(self.equipo_id)
        self.assertEqual(saved["foto"], photo)
        self.assertEqual(saved["modelo"], "Modelo actualizado")

    def test_replace_and_delete_photo(self):
        previous = self.upload()
        current = self.upload()
        self.assertNotEqual(previous, current)
        response = self.save(eliminar_foto="1")
        self.assertEqual(response.headers.get("X-Netwatch-Guardado"), "1")
        self.assertIsNone(db.get_equipo(self.equipo_id)["foto"])

    def test_unsupported_photo_reports_error_and_keeps_previous(self):
        previous = self.upload()
        response = self.save((BytesIO(b"unsupported"), "equipo.heic"))
        self.assertIsNone(response.headers.get("X-Netwatch-Guardado"))
        self.assertIn("formato no compatible", response.get_data(as_text=True))
        self.assertEqual(db.get_equipo(self.equipo_id)["foto"], previous)

    def test_empty_photo_reports_error(self):
        response = self.save((BytesIO(b""), "equipo.png"))
        self.assertIsNone(response.headers.get("X-Netwatch-Guardado"))
        self.assertIn("archivo está vacío", response.get_data(as_text=True))

    def test_disk_error_is_visible_and_keeps_previous(self):
        previous = self.upload()
        with patch("werkzeug.datastructures.FileStorage.save", side_effect=OSError("Disk unavailable")):
            response = self.save((BytesIO(PNG), "equipo.png"))
        self.assertIsNone(response.headers.get("X-Netwatch-Guardado"))
        self.assertIn("No se pudo guardar la imagen", response.get_data(as_text=True))
        self.assertEqual(db.get_equipo(self.equipo_id)["foto"], previous)

    def test_shared_theme_renders_on_main_screens(self):
        endpoints = ["index", "admin.admin", "admin.admin_equipos", "admin.admin_parametros",
                     "tickets.tickets", "disponibilidad.disponibilidad", "topologia.topologia", "ficha"]
        for endpoint in endpoints:
            with self.subTest(endpoint=endpoint), app_module.app.test_request_context():
                params = {"equipo_id": self.equipo_id} if endpoint == "ficha" else {}
                response = self.client.get(url_for(endpoint, **params))
                self.assertEqual(response.status_code, 200)
                self.assertIn("css/netwatch-theme.css", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
