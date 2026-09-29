"""Tus datos de YouTube Music a mano: playlists y links sin tocar la red.

Fuente: data/mymusic_cache.json (lo genera scripts/sync_mymusic.py y se
refresca solo al arrancar si tiene mas de 24h). Todo aca es lectura local:
instantaneo, 0 tokens, funciona hasta sin internet.
"""
import asyncio
import json
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional

TRANQUI_URL = ("https://music.youtube.com/playlist"
               "?list=PLC9x0298xN0rlZZEI8hyQqOclhRjb9aFP&si=3O_1_SpOxYqtfbLU")


def cache_path(vault=None) -> Path:
    if vault is None:
        from core.config import settings
        vault = settings.memory.vault_path
    return Path(vault) / "data" / "mymusic_cache.json"


def load_cache(vault=None) -> Dict[str, Any]:
    try:
        return json.loads(cache_path(vault).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _norm(t: str) -> str:
    t = "".join(c for c in unicodedata.normalize("NFD", (t or "").lower())
                if unicodedata.category(c) != "Mn")
    return " ".join(t.split())


def find_playlist(query: str, vault=None) -> Optional[Dict[str, str]]:
    """Link de una de TUS playlists por nombre aproximado. None si no esta."""
    q = _norm(query)
    if not q:
        return None
    if "tranqui" in q:
        # La Tranqui siempre a mano: URL fija + tracks en cache aunque la
        # lista de playlists se haya vaciado (cookie vencida).
        return {"title": "tranqui", "url": TRANQUI_URL}
    pls = list(load_cache(vault).get("playlists") or [])
    # + playlists de la API oficial (mismo contenido, por si una lista falla)
    try:
        _off = (load_cache(vault).get("official_tracks") or {})
        for _pid, _pl in _off.items():
            if isinstance(_pl, dict) and _pl.get("title"):
                pls.append({"title": _pl["title"],
                            "url": f"https://music.youtube.com/playlist?list={_pid}"})
    except Exception:
        pass
    best = None
    for pl in pls:
        title = str(pl.get("title", ""))
        url = str(pl.get("url", ""))
        if not title or not url:
            continue
        n = _norm(title)
        if n == q or (len(q) >= 3 and q and q in n):
            return {"title": title, "url": url}
        # Match por TOKENS (no substring): "son" no matchea "songs",
        # pero "pop"/"pelis" si matchean "pop"/"pelis de mierda".
        # Tolera plural ("villero" matchea "villeros").
        if best is None and n:
            toks = n.split()
            hit = False
            for w in q.split():
                if len(w) < 3:
                    continue
                if w in toks or (w.endswith("s") and w[:-1] in toks) or (w + "s" in toks):
                    hit = True
                    break
            if hit:
                best = {"title": title, "url": url}
    return best


def _all_cached_tracks(vault=None) -> List[Dict[str, str]]:
    d = load_cache(vault)
    out = []
    for key in ("tranqui_tracks", "liked"):
        for t in (d.get(key) or []):
            if isinstance(t, dict) and t.get("url"):
                out.append(t)
    # + todos los temas de la API oficial (Ponk, rari, GOD!, etc.)
    try:
        for _pl in (d.get("official_tracks") or {}).values():
            for t in ((_pl or {}).get("items") or []):
                if isinstance(t, dict) and t.get("url"):
                    out.append(t)
    except Exception:
        pass
    return out


def pick_cached(source: str = "", artist: str = "",
                exclude_urls: Optional[set] = None, vault=None) -> Optional[Dict[str, str]]:
    """Elige un tema del cache evitando repetidos. source: 'liked', playlist o ''."""
    exclude = set(exclude_urls or [])
    d = load_cache(vault)
    liked = [t for t in (d.get("liked") or []) if isinstance(t, dict) and t.get("url")]
    tranqui = [t for t in (d.get("tranqui_tracks") or []) if isinstance(t, dict) and t.get("url")]
    official = [t for t in _all_cached_tracks(vault)
                if t.get("url") not in {x.get("url") for x in liked + tranqui}]
    s = _norm(source)
    if s == "liked":
        tracks = liked or tranqui
    elif "tranqui" in s:
        tracks = tranqui or liked
    elif s:
        tracks = tranqui + liked + official  # otra playlist: lo mas cercano a mano
    else:
        tracks = liked + tranqui + official  # "algo mio": primero likeds
    a = _norm(artist)
    if a:
        hit = [t for t in tracks if a in _norm(t.get("artists", ""))]
        tracks = hit or tracks
    # Veto a "Algún día" salvo que lo pidan (es el default fantasma que odia).
    tracks = [t for t in tracks if "lgnd" not in _skel(t.get("title", ""))] or tracks
    for t in tracks:
        if t.get("url") not in exclude:
            return t
    return tracks[0] if tracks else None


def _skel(s: str) -> str:
    t = "".join(c for c in unicodedata.normalize("NFD", (s or "").lower())
                if unicodedata.category(c) != "Mn")
    import re as _re
    return _re.sub(r"[^bcdfghjklmnpqrstvwxyz]", "", t)


def cache_age_hours(vault=None) -> float:
    try:
        ts = cache_path(vault).stat().st_mtime
        import time
        return (time.time() - ts) / 3600.0
    except Exception:
        return float("inf")


async def refresh_cache(vault=None) -> Dict[str, Any]:
    """Baja playlists/likeds/historial/Tranqui y reescribe el cache. Best-effort.

    FUSIONA: lo que falla (cookie vencida) conserva lo viejo, jamás se borra
    lo bueno. Un refresh con todo caído deja el cache anterior intacto.
    """
    from datetime import UTC, datetime

    from plugins.ytmusic import Plugin
    p = Plugin()
    await p.initialize(None)
    old = load_cache(vault)
    out: Dict[str, Any] = {
        "synced_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "playlists": list(old.get("playlists") or []),
        "liked": list(old.get("liked") or []),
        "history": list(old.get("history") or []),
        "tranqui_tracks": list(old.get("tranqui_tracks") or []),
        "errors": [],
    }
    r = await p.execute_tool("mymusic_playlists", {"limit": "25"})
    if r.get("ok"):
        out["playlists"] = r["playlists"]
    else:
        out["errors"].append(f"playlists: {r.get('error', r)}")
    r = await p.execute_tool("mymusic_liked", {"limit": "20"})
    if r.get("ok"):
        out["liked"] = r["tracks"]
    else:
        out["errors"].append(f"likeds: {r.get('error', r)}")
    r = await p.execute_tool("mymusic_history", {"limit": "15"})
    if r.get("ok"):
        out["history"] = r["history"]
    else:
        out["errors"].append(f"historial: {r.get('error', r)}")
    tranqui = "https://music.youtube.com/playlist?list=PLC9x0298xN0rlZZEI8hyQqOclhRjb9aFP&si=3O_1_SpOxYqtfbLU"
    r = await p.execute_tool("mymusic_playlist_tracks", {"playlist": tranqui, "limit": "30"})
    if r.get("ok"):
        out["tranqui_tracks"] = r["tracks"]
    else:
        out["errors"].append(f"tranqui: {r.get('error', r)}")
    # API oficial (OAuth permanente): rellena lo que ytmusicapi no pudo.
    try:
        from core.yt_official import sync_official
        off = await asyncio.to_thread(sync_official, vault)
        before = len(out["errors"])
        out = apply_official_sync(out, off)
        if off.get("ok"):
            out["errors"] = [e for e in out["errors"] if "tranqui:" not in e]
        elif len(out["errors"]) == before:
            out["errors"].append(f"oficial: {off.get('error', '')[:150]}")
    except Exception as e:
        out["errors"].append(f"oficial: {str(e)[:150]}")
    try:
        cache_path(vault).write_text(
            json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return out


async def refresh_if_stale(max_hours: float = 24.0, vault=None) -> bool:
    """Refresca en fondo si el cache tiene mas de max_hours. True si refresco."""
    if cache_age_hours(vault) <= max_hours:
        return False
    await refresh_cache(vault)
    return True


def apply_official_sync(out: Dict[str, Any], off: Dict[str, Any]) -> Dict[str, Any]:
    """Fusiona el sync oficial (OAuth permanente) en el cache. Best-effort."""
    if not isinstance(off, dict) or not off.get("ok"):
        return out
    out["playlists"] = off.get("playlists") or out.get("playlists", [])
    out["official_tracks"] = off.get("tracks", {})
    out["official_channel"] = off.get("channel", "")
    for _pid, _pl in (off.get("tracks") or {}).items():
        _items = [t for t in ((_pl or {}).get("items") or [])
                  if isinstance(t, dict) and t.get("url")]
        if not _items:
            continue
        _n = _norm((_pl or {}).get("title", ""))
        if _n == "like":
            out["liked"] = _items
        if "tranqui" in _n:
            out["tranqui_tracks"] = _items
    return out
