"""Instala la cookie de YouTube Music desde un .txt y verifica la cuenta.

Uso:
  1) Guarda el string de la cookie (el que empieza con YSC=...) en
     ~/Desktop/cookie.txt (SOLO ese texto, sin comillas ni JSON).
  2) Corre: .venv\\Scripts\\python scripts\\install_cookie_txt.py
Lee el txt, valida que tenga las claves de sesion, escribe
data/ytmusic_headers.json + ~/Desktop/ytmusic_headers.json y prueba la cuenta.
El .txt original no se borra (borralo vos despues, tiene tu sesion).
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

NEED = ("SID=", "SSID=", "APISID=", "SAPISID=", "HSID=", "LOGIN_INFO=",
        "__Secure-1PSID=", "__Secure-3PSID=")


async def main() -> int:
    src = Path.home() / "Desktop" / "cookie.txt"
    if not src.exists():
        print(f"No encontre {src}. Guarda ahi el string de la cookie y reintenta.")
        return 1
    header = src.read_text(encoding="utf-8").strip().strip('"').strip()
    header = " ".join(header.split())
    missing = [k for k in NEED if k not in header]
    if len(header) < 500 or missing:
        print(f"Ese texto no parece la cookie completa. Faltan: {missing or 'longitud'}")
        print("Tiene que ser el header Cookie entero (empieza con YSC=... y es larguisimo).")
        return 1
    vault = Path(__file__).resolve().parent.parent / "data" / "ytmusic_headers.json"  # noqa: ASYNC240
    try:
        old = json.loads(vault.read_text(encoding="utf-8")) if vault.exists() else {}
    except Exception:
        old = {}
    new = dict(old if isinstance(old, dict) else {})
    new.update({"cookie": header, "origin": "https://music.youtube.com",
                "referer": "https://music.youtube.com/",
                "content-type": "application/json"})
    new.pop("authorization", None)
    vault.write_text(json.dumps(new, ensure_ascii=False), encoding="utf-8")
    desk = Path.home() / "Desktop" / "ytmusic_headers.json"
    desk.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Instalada ({len(header)} chars). Verificando cuenta...")
    from plugins.ytmusic import Plugin
    p = Plugin()
    await p.initialize(None)
    pl = await p.execute_tool("mymusic_playlists", {"limit": "5"})
    print("playlists:", pl if len(str(pl)) < 300 else {k: v for k, v in pl.items() if k != "playlists"})
    se = await p.execute_tool("mymusic_search", {"query": "RAMMA En una"})
    print("search RAMMA:", "OK" if se.get("ok") else se.get("error", se)[:200])
    if pl.get("ok") or se.get("ok"):
        print("Cuenta OK. Borra ~/Desktop/cookie.txt cuando quieras (ya quedo instalada).")
        return 0
    print("La cuenta no responde. Refresca la cookie (logueate de nuevo en Opera GX) y repeti.")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
