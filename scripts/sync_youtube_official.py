"""Sincroniza tus playlists vía API oficial de YouTube (OAuth permanente).

Uso: .venv\\Scripts\\python scripts\\sync_youtube_official.py
Requiere: scripts/setup_ytmusic_oauth.py corrido una vez (ya lo hiciste).
Escribe data/mymusic_cache.json fusionando: playlists, likeds (playlist
"like") y tranqui. No borra lo bueno si algo falla.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def main() -> int:
    from core.mymusic_cache import cache_path, load_cache
    from core.yt_official import sync_official

    res = sync_official(tracks_per_playlist=100)
    if not res.get("ok"):
        print(f"Fallo API oficial: {res.get('error')}")
        return 1
    old = load_cache()
    from core.mymusic_cache import apply_official_sync
    out = apply_official_sync(dict(old), res)
    from datetime import UTC, datetime
    out["synced_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    out["synced_via"] = "youtube-oficial-oauth"
    try:
        cache_path().write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"No pude guardar cache: {e}")
        return 1
    ntr = sum(len(v.get("items", [])) for v in res["tracks"].values())
    print(f"canal: {res.get('channel')} | playlists: {len(res['playlists'])} | "
          f"temas: {ntr} | likeds: {len(out.get('liked', []))} | "
          f"tranqui: {len(out.get('tranqui_tracks', []))}")
    print("guardado en data/mymusic_cache.json (permanente, se refresca solo)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
