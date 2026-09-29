"""Paso 2: completa el login OAuth tras autorizar en el navegador y sincroniza."""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


async def main() -> int:
    cid = os.getenv("YTMUSIC_CLIENT_ID", "").strip()
    csec = os.getenv("YTMUSIC_CLIENT_SECRET", "").strip()
    vault = Path(__file__).resolve().parent.parent  # noqa: ASYNC240 - script CLI de un solo uso
    pend = vault / "data" / ".oauth_pending.json"
    if not pend.exists():
        print("No hay login pendiente. Corre scripts/start_ytmusic_oauth.py primero.")
        return 1
    device_code = json.loads(pend.read_text(encoding="utf-8"))["device_code"]
    from ytmusicapi.auth.oauth.credentials import OAuthCredentials
    from ytmusicapi.auth.oauth.token import RefreshingToken

    creds = OAuthCredentials(cid, csec)
    try:
        raw = creds.token_from_code(device_code)
    except Exception as e:
        print(f"Google devolvió error: {str(e)[:200]}")
        print("Si dice 'authorization pending', volvé al navegador y aceptá con tu cuenta primero.")
        return 1
    if "error" in raw:
        print(f"Google: {raw.get('error')} - {raw.get('error_description', '')[:200]}")
        print("Autorizá en el navegador primero y reintentá.")
        return 1
    token = RefreshingToken(credentials=creds, **{k: v for k, v in raw.items()
                                                   if k in ("scope", "token_type", "access_token",
                                                            "refresh_token", "expires_at", "expires_in")})
    out = vault / "data" / "ytmusic_oauth.json"
    out.write_text(json.dumps({
        "scope": token.scope,
        "token_type": token.token_type,
        "access_token": token.access_token,
        "refresh_token": token.refresh_token,
        "expires_at": token.expires_at,
        "expires_in": token.expires_in,
    }), encoding="utf-8")
    try:
        pend.unlink()
    except OSError:
        pass
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
        return 1
    from core.mymusic_cache import refresh_cache
    res = await refresh_cache()
    print(f"playlists: {len(res['playlists'])} | likeds: {len(res['liked'])} | "
          f"historial: {len(res['history'])} | tranqui: {len(res['tranqui_tracks'])}")
    print("Listo: sesión permanente, no más cookies.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
