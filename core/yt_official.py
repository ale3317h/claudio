"""YouTube Data API v3 oficial con tu OAuth (permanente, no vence).

A diferencia de ytmusicapi (cookie/OAuth interno, frágil), esta API oficial
acepta tu refresh_token y trae tus playlists + temas siempre.
Cubre: playlists (14) y likeds (playlist "like", 86 temas).
NO cubre: historial de reproducción (Google no lo expone acá).
Cuota: ~15 unidades por sync completo (límite diario 10.000).
"""
import json
import os
from pathlib import Path
from typing import Any, Dict, List

YT_SCOPE = "https://www.googleapis.com/auth/youtube"


def _vault() -> Path:
    from core.config import settings
    return Path(settings.memory.vault_path)


def oauth_file(vault=None) -> Path:
    return (Path(vault) if vault else _vault()) / "data" / "ytmusic_oauth.json"


def load_credentials(vault=None):
    """Credentials de Google con auto-refresh. None si falta algo."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    cid = os.getenv("YTMUSIC_CLIENT_ID", "").strip()
    csec = os.getenv("YTMUSIC_CLIENT_SECRET", "").strip()
    p = oauth_file(vault)
    if not (cid and csec and p.exists()):
        return None
    try:
        tok = json.loads(p.read_text(encoding="utf-8"))
        if "refresh_token" not in tok:
            return None
    except Exception:
        return None
    try:
        from google.oauth2.credentials import Credentials
        return Credentials(token=tok.get("access_token"), refresh_token=tok["refresh_token"],
                           token_uri="https://oauth2.googleapis.com/token",  # noqa: S106 - nombre de parámetro OAuth, no secreto
                           client_id=cid, client_secret=csec, scopes=[YT_SCOPE])
    except Exception:
        return None


def _service(creds):
    from googleapiclient.discovery import build
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def fetch_playlists(svc, mine: bool = True) -> List[Dict[str, Any]]:
    out, token = [], None
    while True:
        req = svc.playlists().list(mine=mine, part="snippet,contentDetails",
                                   maxResults=50, pageToken=token) if mine else None
        res = req.execute()
        for p in res.get("items", []):
            pid = p.get("id", "")
            if pid:
                out.append({"id": pid, "title": p["snippet"].get("title", "sin titulo"),
                            "count": p["contentDetails"].get("itemCount", 0)})
        token = res.get("nextPageToken")
        if not token:
            break
    return out


def fetch_tracks(svc, playlist_id: str, limit: int = 50) -> List[Dict[str, str]]:
    out, token = [], None
    while len(out) < limit:
        res = svc.playlistItems().list(playlistId=playlist_id, part="snippet,contentDetails",
                                       maxResults=min(50, limit - len(out)),
                                       pageToken=token).execute()
        for it in res.get("items", []):
            sn = it.get("snippet", {})
            vid = (sn.get("resourceId", {}) or {}).get("videoId", "")
            if not vid:
                continue
            out.append({"title": sn.get("title", "sin titulo"),
                        "artists": sn.get("videoOwnerChannelTitle", ""),
                        "url": f"https://www.youtube.com/watch?v={vid}"})
        token = res.get("nextPageToken")
        if not token or len(out) >= limit:
            break
    return out


def channel_name(svc) -> str:
    try:
        res = svc.channels().list(mine=True, part="snippet").execute()
        items = res.get("items", [])
        return items[0]["snippet"]["title"] if items else ""
    except Exception:
        return ""


def sync_official(vault=None, tracks_per_playlist: int = 50) -> Dict[str, Any]:
    """Baja playlists + temas vía API oficial. Best-effort, nunca levanta."""
    creds = load_credentials(vault)
    if creds is None:
        return {"ok": False, "error": "Sin OAuth: corre scripts/setup_ytmusic_oauth.py una vez"}
    try:
        svc = _service(creds)
        who = channel_name(svc)
        pls = fetch_playlists(svc)
        data: Dict[str, Any] = {"channel": who, "playlists": [], "tracks": {}}
        for p in pls:
            data["playlists"].append({"title": p["title"],
                                      "url": f"https://music.youtube.com/playlist?list={p['id']}"})
            try:
                data["tracks"][p["id"]] = {"title": p["title"],
                                           "items": fetch_tracks(svc, p["id"], tracks_per_playlist)}
            except Exception as e:
                data["tracks"][p["id"]] = {"title": p["title"], "items": [],
                                           "error": str(e)[:150]}
        data["ok"] = True
        return data
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}
