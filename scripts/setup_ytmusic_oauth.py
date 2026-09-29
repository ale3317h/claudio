"""Login permanente de YouTube Music via OAuth (una sola vez, no vence).

Por qué: la cookie de Opera GX (data/ytmusic_headers.json) es método deprecated
y se vence cada días/semanas. OAuth usa refresh_token y se auto-renueva solo.

Pasos previos (5 min, una sola vez):
  1) https://console.cloud.google.com > proyecto nuevo > habilitar "YouTube Data API v3"
  2) APIs y servicios > Credenciales > Crear credencial > ID de cliente OAuth
     > tipo "TVs and Limited Input devices" > anotá client_id y client_secret
  3) Corre: .venv\\Scripts\\python scripts\\setup_ytmusic_oauth.py
     Te pide client_id/secret, abre el navegador, logueás tu cuenta de YouTube,
     y guarda data/ytmusic_oauth.json. Listo para siempre.

Opcional: guardá client_id/secret en .env como YTMUSIC_CLIENT_ID / YTMUSIC_CLIENT_SECRET
para que el refresh automático funcione sin pedirlos de nuevo.
"""
import asyncio
import getpass
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _vault() -> Path:
    return Path(__file__).resolve().parent.parent


async def main() -> int:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    cid = os.getenv("YTMUSIC_CLIENT_ID", "").strip()
    csec = os.getenv("YTMUSIC_CLIENT_SECRET", "").strip()
    if not cid:
        print("Pegá tu client_id de Google Cloud (TVs and Limited Input devices):")
        cid = input("client_id: ").strip()  # noqa: ASYNC250 - script CLI interactivo de un solo uso
    if not csec:
        csec = getpass.getpass("client_secret (no se muestra): ").strip()
    if not cid or not csec:
        print("Faltan credenciales. Crealas en Google Cloud Console y reintentá.")
        return 1
    out = _vault() / "data" / "ytmusic_oauth.json"
    print("Abriendo flujo OAuth en tu navegador... logueá tu cuenta de YouTube.")
    try:
        from ytmusicapi import setup_oauth
        setup_oauth(client_id=cid, client_secret=csec,
                    filepath=str(out), open_browser=True)
    except Exception as e:
        print(f"OAuth falló: {str(e)[:300]}")
        print("Tip: si dice 'invalid client', revisá que el tipo sea TVs and Limited Input devices.")
        return 1
    try:
        d = json.loads(out.read_text(encoding="utf-8"))
        assert "refresh_token" in d and "access_token" in d
    except Exception:
        print(f"No quedó bien {out}. Reintentá.")
        return 1
    print(f"Guardado en {out}. Verificando cuenta...")
    os.environ["YTMUSIC_CLIENT_ID"] = cid
    os.environ["YTMUSIC_CLIENT_SECRET"] = csec
    from plugins.ytmusic import Plugin
    p = Plugin()
    await p.initialize(None)
    pl = await p.execute_tool("mymusic_playlists", {"limit": "5"})
    se = await p.execute_tool("mymusic_search", {"query": "RAMMA En una"})
    print("playlists:", "OK" if pl.get("ok") else str(pl.get("error", pl))[:200])
    print("search:", "OK" if se.get("ok") else str(se.get("error", se))[:200])
    if not pl.get("ok") and not se.get("ok"):
        print("La cuenta no responde. Revisá el login y reintentá.")
        return 1
    print("Cuenta OK. Ahora sincronizo tu cache local...")
    from core.mymusic_cache import refresh_cache
    res = await refresh_cache()
    print(f"playlists: {len(res['playlists'])} | likeds: {len(res['liked'])} | "
          f"historial: {len(res['history'])} | tranqui: {len(res['tranqui_tracks'])}")
    print("Listo: no tenés que actualizar más la cookie. "
          "Guardá client_id/secret en .env (YTMUSIC_CLIENT_ID/_SECRET) y borrá este mensaje.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
