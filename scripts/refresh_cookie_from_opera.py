"""Extrae la cookie de YouTube Music desde tu Opera GX real y la actualiza.

Lee (sin cerrar Opera): %APPDATA%/Opera Software/Opera GX Stable/Default/Network/Cookies
Desencripta con DPAPI (tu usuario de Windows) y arma el header.
Escribe: data/ytmusic_headers.json + ~/Desktop/ytmusic_headers.json (copia pedida).
Uso: .venv\\Scripts\\python scripts\\refresh_cookie_from_opera.py
No instala nada nuevo (usa sqlite3 + cryptography, ya en el venv).
"""
import base64
import ctypes
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

WANT_HOSTS = ("music.youtube.com", ".music.youtube.com", "youtube.com", ".youtube.com",
              "www.youtube.com", ".www.youtube.com")
# Cookies clave que usa la API; si faltan estas, la libreria privada no abre.
KEY_COOKIES = ("SID", "SSID", "APISID", "SAPISID", "HSID", "LOGIN_INFO",
               "__Secure-1PSID", "__Secure-3PSID", "SIDCC")


def _opera_base() -> Path:
    return Path(os.environ.get("APPDATA", "")) / "Opera Software" / "Opera GX Stable"


def _master_key(local_state: Path) -> bytes:
    raw = json.loads(local_state.read_text(encoding="utf-8"))
    enc = base64.b64decode(raw["os_crypt"]["encrypted_key"])[5:]  # saca prefijo DPAPI
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf_in = ctypes.create_string_buffer(enc, len(enc))
    blob_in = DATA_BLOB(len(enc), ctypes.cast(buf_in, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise RuntimeError("DPAPI no pudo desbloquear la clave (corre como tu usuario, sin admin raro)")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _decrypt_cookie(enc: bytes, master: bytes) -> str:
    if not enc:
        return ""
    if enc[:3] in (b"v10", b"v11", b"v80"):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce, payload = enc[3:15], enc[15:]
        return AESGCM(master).decrypt(nonce, payload, None).decode("utf-8", "replace")
    try:
        return enc.decode("utf-8")
    except Exception:
        return ""


def main() -> int:
    base = _opera_base()
    cookies_src = base / "Default" / "Network" / "Cookies"
    local_state = base / "Local State"
    if not cookies_src.exists():
        print(f"No encontre cookies en {cookies_src}")
        return 1
    if not local_state.exists():
        print(f"No encontre {local_state}")
        return 1
    master = _master_key(local_state)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "Cookies"
        try:
            shutil.copy2(cookies_src, tmp)
        except PermissionError:
            print("Opera GX esta abierto y bloquea el archivo de cookies (WinError 32). "
                  "Cerra Opera GX del todo (todas las ventanas), volve a correr este script "
                  "(tarda 3 segundos), y reabri Opera. No se pierde nada: solo lee la sesion.")
            return 2
        con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT host_key, name, value, encrypted_value FROM cookies "
                "WHERE host_key IN (?,?,?,?,?,?)", WANT_HOSTS).fetchall()
        finally:
            con.close()
    jar = {}
    for _host, name, value, enc in rows:
        v = (value or "").strip() or _decrypt_cookie(bytes(enc or b""), master)
        if v:
            jar[name] = v  # ultimo gana
    missing = [k for k in KEY_COOKIES if k not in jar]
    header = "; ".join(f"{k}={v}" for k, v in sorted(jar.items()))
    print(f"cookies youtube encontradas: {len(jar)} | faltan clave: {missing or 'ninguna'}")
    if len(header) < 500 or missing:
        print("La sesion de Opera GX no tiene login completo de YouTube. "
              "Abri music.youtube.com logueado en Opera GX, recarga, y reintenta.")
        return 1
    # Actualiza vault + copia en escritorio (pedida)
    vault = Path(__file__).resolve().parent.parent / "data" / "ytmusic_headers.json"
    try:
        old = json.loads(vault.read_text(encoding="utf-8")) if vault.exists() else {}
    except Exception:
        old = {}
    new = dict(old if isinstance(old, dict) else {})
    new.update({"cookie": header, "origin": "https://music.youtube.com",
                "referer": "https://music.youtube.com/",
                "content-type": "application/json"})
    new.pop("authorization", None)  # CLAUDIO la regenera por llamada
    vault.write_text(json.dumps(new, ensure_ascii=False), encoding="utf-8")
    desk = Path.home() / "Desktop" / "ytmusic_headers.json"
    desk.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK: {vault} + copia en {desk} ({len(header)} chars, {len(jar)} cookies)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
