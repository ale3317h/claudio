"""Sincroniza tus gustos de YouTube Music (via cookie de Opera GX) a un cache local.

Uso: .venv\\Scripts\\python scripts\\sync_mymusic.py
Si falla con error de cookie, refrescala desde Opera GX (music.youtube.com >
F12 > Application > Cookies) y pegala en data/ytmusic_headers.json.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def main() -> int:
    from core.mymusic_cache import refresh_cache
    out = await refresh_cache()
    print(f"playlists: {len(out['playlists'])} | likeds: {len(out['liked'])} | "
          f"historial: {len(out['history'])} | tranqui: {len(out['tranqui_tracks'])}")
    print("guardado en data/mymusic_cache.json")
    if out["errors"]:
        print("AVISOS:")
        for e in out["errors"]:
            print(f" - {e[:300]}")
        print("\nSi ves 'cookie vencida': refrescala desde Opera GX y pegala en "
              "data/ytmusic_headers.json.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
