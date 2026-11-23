# Genera android/colector_apk_config.json a partir de colector_config.json.
# Usa la cuenta "telefono" (solo lectura) si esta completa; si no, la del colector.
import json, os, sys

aqui = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(aqui, "colector_config.json"), encoding="utf-8") as f:
    cfg = json.load(f)
fb = cfg["firebase"]
tel = cfg.get("telefono") or {}
email = tel.get("email") or fb["email"]
password = tel.get("password") or fb["password"]
if not password or password == "CAMBIAR":
    print("[ERROR] Falta la clave en colector_config.json")
    sys.exit(1)
out = {"databaseURL": fb["databaseURL"], "apiKey": fb["apiKey"], "email": email, "password": password}
with open(os.path.join(aqui, "android", "colector_apk_config.json"), "w", encoding="utf-8") as f:
    json.dump(out, f)
print("[OK] Datos de Firebase listos para la app (cuenta: %s)" % email)
