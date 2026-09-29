"""Paso 1: genera el código de login de YouTube Music (no pide nada interactivo)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass
import os

cid = os.getenv("YTMUSIC_CLIENT_ID", "").strip()
csec = os.getenv("YTMUSIC_CLIENT_SECRET", "").strip()
assert cid and csec, "Faltan YTMUSIC_CLIENT_ID/_SECRET en .env"

from ytmusicapi.auth.oauth.credentials import OAuthCredentials

creds = OAuthCredentials(cid, csec)
code = creds.get_code()
print("URL:", code.get("verification_url"))
print("CODIGO:", code.get("user_code"))
print("Expira en (s):", code.get("expires_in"))
pend = Path(__file__).resolve().parent.parent / "data" / ".oauth_pending.json"
pend.write_text(json.dumps({"device_code": code["device_code"]}), encoding="utf-8")
print("Pendiente guardado. Tras autorizar en el navegador, corre scripts/finish_ytmusic_oauth.py")
