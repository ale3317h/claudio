import hashlib
import os
import re
import time
from typing import Any, Dict, List

from plugins.base import IntentPattern, Permission, PluginBase, PluginManifest, ToolParameter, ToolSchema


def _headers_path() -> str:
    custom = os.getenv("YTMUSIC_HEADERS", "").strip()
    if custom:
        return custom
    from core.config import settings
    return str(settings.memory.vault_path / "data" / "ytmusic_headers.json")


def _oauth_path() -> str:
    custom = os.getenv("YTMUSIC_OAUTH", "").strip()
    if custom:
        return custom
    from core.config import settings
    return str(settings.memory.vault_path / "data" / "ytmusic_oauth.json")


def _is_oauth_file(path: str) -> bool:
    try:
        import json
        with open(path, encoding="utf-8") as f:
            d = json.loads(f.read())
        return isinstance(d, dict) and "refresh_token" in d and "access_token" in d
    except Exception:
        return False


REFRESH_STEPS = (
    "La sesion de YouTube Music fallo. Fix definitivo (una sola vez, no vence): "
    ".venv\\Scripts\\python scripts\\setup_ytmusic_oauth.py "
    "(pide client_id/secret de YouTube Data API y loguea tu cuenta; genera data/ytmusic_oauth.json). "
    "Parche rapido: .venv\\Scripts\\python scripts\\refresh_cookie_from_opera.py "
    "con Opera GX CERRADO del todo. Mientras tanto uso tu cache local."
)


def _strip_compound_scaffold(text: str) -> str:
    """Pedido compuesto ('primero quita esto y despues pone X'): lo que vale
    para BUSCAR es lo de despues del 'despues'. Sin 'despues', saca la
    clausula del 'primero ...' que describe la OTRA accion."""
    import unicodedata as _ud
    t = "".join(c for c in _ud.normalize("NFD", (text or "").lower())
                if _ud.category(c) != "Mn")
    if "despues" in t:
        t = t.split("despues")[-1]
    t = re.sub(r"\bprimero\b.*?(,|\by\b|$)", " ", t)
    return " ".join(t.split())


def _clean_album_query(text: str) -> str:
    """Saca verbos/relleno y deja artista/album: 'poner algun album de Paulo Londra' -> 'Paulo Londra'."""
    import unicodedata as _ud
    raw = "".join(c for c in _ud.normalize("NFD", (text or "").lower())
                  if _ud.category(c) != "Mn")
    wants_rv = bool(re.search(r"\br[,.\s]*b[,.\s]*r\b|\brb\b", raw))
    t = _strip_compound_scaffold(text)
    # "RB" / "r b r" dictado = album RV de RAMMA (solo en contexto de album).
    t = re.sub(r"\br[,.\s]*b[,.\s]*r\b|\brb\b", "RV", t, flags=re.IGNORECASE)
    stop = {"pone", "pon", "poner", "ponele", "reproduci", "reproducir", "reproduce",
            "pasa", "pasame", "busca", "buscar", "un", "una", "algun", "alguna",
            "algunos", "album", "albumes", "disco", "completo", "entero", "de",
            "del", "en", "el", "la", "mi", "por", "favor", "escuchar", "musica",
            "quita", "quitar", "quitate", "saca", "sacar", "limpia", "limpiar",
            "limpie", "primero", "despues", "este", "esta", "esto", "ese", "esa",
            "eso", "y", "que", "si", "pero", "lo", "los", "las", "les",
            "se", "solo", "solamente", "ya", "ahora", "ahi", "alla", "aca",
            "tema", "temas", "cancion", "canciones", "ponelo", "ponela", "pasalo"}
    toks = [w for w in re.sub(r"[^\w]", " ", t).split()
            if w not in stop and len(w) > 1]
    q = " ".join(toks)
    q = re.sub(r"\bram+a\b|\bcarb[yi]\b|\bramba\b|\bsramma\b", "RAMMA", q, flags=re.IGNORECASE)
    q = re.sub(r"\bpa[bu]l[ao]\s+londra\b|\bpobl[ao]\s+londr?a\b|\bpobl[ao]dondra\b|\bpoblolondra\b|\bpobl[ao]\s+(?:de\s+)?londra\b",
               "Paulo Londra", q, flags=re.IGNORECASE)
    q = " ".join(q.split())
    if wants_rv and "RV" not in q.split():
        q = f"RV {q}".strip()
    return q


def _parse_playlist_id(text: str) -> str:
    import urllib.parse as _up
    t = (text or "").strip()
    if not t:
        return ""
    if t.startswith("http"):
        try:
            q = _up.parse_qs(_up.urlparse(t).query)
            if q.get("list"):
                return q["list"][0].strip()
        except Exception:
            pass
    # ID crudo de playlist (empieza con PL, VL, RD, OLAK...)
    m = re.search(r"(PL[\w-]{10,}|VL[\w-]{10,}|RD[\w-]{10,}|OLAK[\w-]{10,}|PLC9[\w-]+)", t)
    if m:
        return m.group(1)
    return t if len(t) > 8 and " " not in t else ""


def _build_oauth_api():
    """Instancia OAuth o None. OJO: ytmusicapi 1.12.x devuelve HTTP 400 con
    OAuth en algunas cuentas (bug upstream #813) aunque el token sea válido."""
    from ytmusicapi import YTMusic
    oauth = _oauth_path()
    if not (oauth and os.path.exists(oauth) and _is_oauth_file(oauth)):
        return None
    try:
        from ytmusicapi import OAuthCredentials
        cid = os.getenv("YTMUSIC_CLIENT_ID", "").strip()
        csec = os.getenv("YTMUSIC_CLIENT_SECRET", "").strip()
        if cid and csec:
            return YTMusic(auth=oauth,
                           oauth_credentials=OAuthCredentials(client_id=cid, client_secret=csec))
        return YTMusic(auth=oauth)
    except Exception:
        return None


def _build_cookie_api():
    """Instancia por cookie o None si no hay archivo válido."""
    import json

    from ytmusicapi import YTMusic
    path = _headers_path()
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            stored = json.loads(f.read())
    except Exception:
        return None
    if not isinstance(stored, dict) or "cookie" not in stored:
        return None
    headers = dict(stored)
    m = re.search(r"SAPISID=([^;]+)", headers.get("cookie", ""))
    if m:
        ts = int(time.time())
        origin = "https://music.youtube.com"
        h = hashlib.sha1(f"{ts} {m.group(1)} {origin}".encode()).hexdigest()  # noqa: S324 - requerido por protocolo SAPISIDHASH
        headers["authorization"] = f"SAPISIDHASH {ts}_{h}"
    try:
        return YTMusic(auth=headers)
    except Exception:
        try:
            return YTMusic(auth=path)
        except Exception:
            return None


_AUTH_FAIL_HINTS = ("400", "401", "403", "auth", "cookie", "login",
                    "unauthor", "forbidden", "invalid argument")


def _api_all():
    """Todas las sesiones disponibles en orden: cookie primero (probada,
    funciona), OAuth después (permanente cuando upstream lo arregle)."""
    apis = []
    for builder in (_build_cookie_api, _build_oauth_api):
        try:
            api = builder()
        except Exception:
            api = None
        if api is not None:
            apis.append(api)
    return apis


def _try_each(method: str, *args, **kwargs):
    """Llama a method en cada sesión disponible. Devuelve (True, resultado)
    con la primera que funcione, o (False, último error). Si el error NO es
    de auth (ej: playlist inexistente), no reintenta con la otra sesión."""
    apis = _api_all()
    if not apis:
        raise RuntimeError(
            "Sin sesion de YouTube Music. Fix definitivo (no vence): "
            ".venv\\Scripts\\python scripts\\setup_ytmusic_oauth.py "
            "y seguí los pasos del navegador. " + REFRESH_STEPS
        )
    last_err = None
    for api in apis:
        try:
            return True, getattr(api, method)(*args, **kwargs)
        except Exception as e:
            last_err = e
            if not any(h in str(e).lower() for h in _AUTH_FAIL_HINTS):
                break  # error funcional (no de sesión): no tiene sentido reintentar
    return False, last_err


def _api():
    apis = _api_all()
    if not apis:
        raise RuntimeError(
            "Sin sesion de YouTube Music. Fix definitivo (no vence): "
            ".venv\\Scripts\\python scripts\\setup_ytmusic_oauth.py "
            "y seguí los pasos del navegador. " + REFRESH_STEPS
        )
    return apis[0]


class Plugin(PluginBase):
    manifest = PluginManifest(
        name="ytmusic",
        version="1.0.0",
        description="Tu cuenta de YouTube Music: playlists, historial y gustos",
        author="CLAUDIO",
        category="media",
        dependencies=[],
        permissions=[Permission.NETWORK],
    )

    async def initialize(self, core) -> None:
        from core.config import settings
        from core.store import Store
        self.store = Store(settings.memory.vault_path / "data")
        self.core = core

    async def shutdown(self) -> None:
        pass

    def _recent_picks(self, limit: int = 15) -> List[Dict[str, str]]:
        """Lo último que pusiste vía CLAUDIO (tu 'volver a escuchar' local)."""
        out, seen = [], set()
        try:
            import json as _json
            for event in self.store.recent_events(kind="musica", limit=50):
                try:
                    data = _json.loads(event.get("content", ""))
                except Exception:  # noqa: S112 - eventos viejos corruptos se saltean
                    continue
                if isinstance(data, dict) and data.get("url") and data["url"] not in seen:
                    seen.add(data["url"])
                    out.append({"title": data.get("title", "sin titulo"),
                                "artists": data.get("artists", ""),
                                "url": data["url"]})
                if len(out) >= limit:
                    break
        except Exception:
            pass
        return out

    async def tool_mymusic_recent(self, limit: str = "10") -> Dict[str, Any]:
        """Tu 'volver a escuchar': lo último que sonó vía CLAUDIO (local, siempre anda)."""
        try:
            n = max(1, min(int(str(limit or "10").strip()), 30))
        except (TypeError, ValueError):
            n = 10
        picks = self._recent_picks(n)
        if not picks:
            return {"error": "Todavía no hay historial local: pedime música y lo armo solo"}
        return {"ok": True, "tracks": picks, "source": "tu historial CLAUDIO"}

    async def tool_mymusic_forgotten(self, limit: str = "10") -> Dict[str, Any]:
        """Tus 'favoritos olvidados': likeds que hace rato no suenan."""
        try:
            n = max(1, min(int(str(limit or "10").strip()), 30))
        except (TypeError, ValueError):
            n = 10
        from core.mymusic_cache import load_cache
        liked = [t for t in (load_cache().get("liked") or [])
                 if isinstance(t, dict) and t.get("url")]
        if not liked:
            return {"error": "Sin likeds en cache: corre scripts/sync_youtube_official.py"}
        played = self._played_urls(limit=60)
        fresh = [t for t in liked if t["url"] not in played] or liked
        return {"ok": True, "tracks": fresh[:n], "source": "tus likeds no recientes"}

    def _played_urls(self, limit: int = 20) -> set:
        urls = set()
        try:
            import json as _json
            for event in self.store.recent_events(kind="musica", limit=limit):
                try:
                    data = _json.loads(event.get("content", ""))
                    if isinstance(data, dict) and data.get("url"):
                        urls.add(data["url"])
                except Exception:
                    pass
        except Exception:
            pass
        return urls

    async def tool_mymusic_cached_play(self, source: str = "", artist: str = "") -> Dict[str, Any]:
        """Pone algo TUYO al instante: elige del cache local (0 red, 0 espera).

        source: nombre de playlist ("tranqui"), "liked" o vacio (=tus likeds).
        artist: filtra por artista (RAMMA, Paulo Londra...). Salta lo ya sonado.
        """
        import re as _re

        from core.mymusic_cache import pick_cached
        artist = _re.sub(r"\bram+a\b|\bcarb[yi]\b|\bramba\b", "RAMMA",
                         (artist or "").strip(), flags=_re.IGNORECASE)
        artist = _re.sub(r"\bpa[bu]l[ao]\s+londra\b|\bpobl[ao]\s+londr?a\b"
                         r"|\bpobl[ao]dondra\b|\bpoblolondra\b",
                         "Paulo Londra", artist, flags=_re.IGNORECASE)
        pick = pick_cached(source=source, artist=artist,
                           exclude_urls=self._played_urls())
        if not pick:
            return {"error": "Cache vacio: corre scripts/sync_mymusic.py o usa mymusic_search"}
        try:
            import json as _json
            self.store.log_event("musica", _json.dumps(pick, ensure_ascii=False))
        except Exception:
            pass
        return {"ok": True, "pick": pick, "source": "tu cache (instantaneo)"}

    async def tool_mymusic_playlists(self, limit: str = "25") -> Dict[str, Any]:
        try:
            n = max(1, min(int(str(limit).strip()), 50))
        except (TypeError, ValueError):
            n = 25
        # PRIMERO tu memoria local (API oficial, permanente): la sesion
        # ytmusicapi esta rota y devolvia [] -> "no tenes playlists" (mentira).
        try:
            from core.mymusic_cache import load_cache
            cached = [p for p in (load_cache().get("playlists") or [])
                      if isinstance(p, dict) and p.get("title") and p.get("url")]
            if cached:
                return {"ok": True, "playlists": cached[:n],
                        "source": "tu biblioteca (memoria)"}
        except Exception:
            pass
        try:
            ok, items = _try_each("get_library_playlists", limit=n)
        except RuntimeError as e:
            return {"error": str(e)}
        if not ok:
            return {"error": f"No pude leer tus playlists: {str(items)[:200]}"}
        items = items or []
        out = []
        for pl in items:
            pid = (pl.get("playlistId") or "").strip()
            if not pid:
                continue
            out.append({
                "title": pl.get("title", "sin titulo"),
                "url": f"https://music.youtube.com/playlist?list={pid}",
            })
        return {"ok": True, "playlists": out}

    async def tool_mymusic_history(self, limit: str = "10") -> Dict[str, Any]:
        try:
            n = max(1, min(int(str(limit).strip()), 25))
        except (TypeError, ValueError):
            n = 10
        try:
            ok, items = _try_each("get_history")
        except RuntimeError as e:
            return {"error": str(e)}
        if not ok:
            return {"error": f"No pude leer tu historial (sesión vencida?): {str(items)[:150]}. " + REFRESH_STEPS}
        items = items or []
        out = []
        for it in items[:n]:
            artists = ", ".join(a.get("name", "") for a in (it.get("artists") or []) if a.get("name"))
            out.append({"title": it.get("title", "sin titulo"), "artists": artists})
        return {"ok": True, "history": out}

    def _track_out(self, it: Dict[str, Any]) -> Dict[str, str]:
        vid = (it.get("videoId") or "").strip()
        artists = ", ".join(a.get("name", "") for a in (it.get("artists") or []) if a.get("name"))
        album = it.get("album")
        return {
            "title": it.get("title", "sin titulo"),
            "artists": artists,
            "album": (album.get("name", "") if isinstance(album, dict) else "") or "",
            "url": f"https://www.youtube.com/watch?v={vid}" if vid else "",
        }

    async def tool_mymusic_search(self, query: str) -> Dict[str, Any]:
        query = _strip_compound_scaffold(query or "")
        query = query.strip()
        query = re.sub(r"\bram+a\b|\bcarb[yi]\b|\bramba\b", "RAMMA",
                       query, flags=re.IGNORECASE)
        query = re.sub(r"\bpa[bu]l[ao]\s+londra\b|\bpobl[ao]\s+londr?a\b"
                       r"|\bpobl[ao]dondra\b|\bpoblolondra\b",
                       "Paulo Londra", query, flags=re.IGNORECASE)
        query = re.sub(r"\bskijet\b|\bskyjet\b|\bskyshet\b|\bskilet\b|\bskillets?\b"
                       r"|\bsquilet\b|\besquilet\b|\beskillet\b|\bskidget\b|\bskidjet\b"
                       r"|\bkitschets?\b|\bkitshets?\b|\bkischets?\b",
                       "Skillet", query, flags=re.IGNORECASE)
        query = re.sub(r"\braze\b|\braice\b|\brai\b", "Rise", query, flags=re.IGNORECASE)
        query = re.sub(r"\b(saga|aga|saja)\s+boys?\b|\bsaja\s+b\b", "Saja Boys", query, flags=re.IGNORECASE)
        query = re.sub(r"\b(vichero|vichera|viyero|viyera|biyero|biyera|villegru|vilegru)\b",
                       "villero", query, flags=re.IGNORECASE)
        query = re.sub(r"\b(gonna|gona|wana|wanna|yana)\s+(fly|flight|split|flay)\s+now\b",
                       "Gonna Fly Now Bill Conti", query, flags=re.IGNORECASE)
        query = re.sub(r"\brocky\b|\bracky\b|\braky\b|\broky\b|\broki\b|\brucky\b"
                       r"|\brackynolds\b|\brockynolds\b",
                       "Gonna Fly Now Bill Conti", query, flags=re.IGNORECASE)
        query = re.sub(r"\boro\s+beso\b", "Otro beso", query, flags=re.IGNORECASE)
        # Exclusiones de version NO se buscan ("pero no en vivo" rankea en vivo
        # en YouTube: no hay NOT). Se filtra al elegir, no al buscar.
        query = re.sub(
            r"\bpero\s+no\s+(?:en\s+vivo|ac[úu]stico|en\s+directo|remix|cover|live|en\s+espa[ñn]ol|espa[ñn]ol)\b"
            r"|\b(?:no|sin)\s+(?:en\s+vivo|ac[úu]stico|en\s+directo|remix|cover|live)\b"
            r"|\bque\s+no\s+sea\s+(?:en\s+vivo|ac[úu]stico|en\s+directo|remix|cover|live|[a-záéíóúñ]{3,}(?:\s+[a-záéíóúñ]{2,}){0,2})\b",
            " ", query, flags=re.IGNORECASE)
        query = " ".join(query.split()).strip(" ,.!?¿¡\"'")
        if not query:
            return {"error": "Indica que buscar en tu cuenta"}
        try:
            # search usa tu sesion: prioriza lo que escuchas/guardas.
            ok, items = _try_each("search", query, filter="songs", limit=5)
        except RuntimeError as e:
            return {"error": str(e)}
        if not ok:
            return {"error": f"No pude buscar: {str(items)[:200]}"}
        items = items or []
        out = []
        for it in items:
            t = self._track_out(it)
            if t["url"]:
                out.append(t)
        if not out:
            return {"error": f"Sin resultados en tu cuenta para '{query}'"}
        return {"ok": True, "query": query, "results": out, "source": "tu cuenta (ranking personalizado)"}

    def _playlist_out(self, it: Dict[str, Any]) -> Dict[str, str]:
        pid = (it.get("playlistId") or "").strip()
        if not pid:
            br = (it.get("browseId") or "").strip()
            if br.startswith("VL"):
                pid = br[2:]
        author = it.get("author")
        if isinstance(author, list):
            author = ", ".join(a.get("name", "") for a in author if a.get("name"))
        return {
            "title": it.get("title", "sin titulo"),
            "author": author or "",
            "url": f"https://music.youtube.com/playlist?list={pid}" if pid else "",
        }

    async def tool_search_playlists(self, query: str) -> Dict[str, Any]:
        """Buscar playlists PUBLICAS de YouTube Music (NO tu biblioteca).

        Para 'busca una playlist de Milo J': muestra opciones de afuera.
        Si el pedido trae verbo de poner, el cerebro manda la primera a Discord.
        """
        query = _strip_compound_scaffold(query or "")
        query = re.sub(r"\bram+a\b|\bcarb[yi]\b|\bramba\b", "RAMMA",
                       query, flags=re.IGNORECASE)
        if not query:
            return {"error": "Indica de quién o qué buscar playlists"}
        try:
            ok, items = _try_each("search", query, filter="playlists", limit=5)
        except RuntimeError as e:
            return {"error": str(e)}
        if not ok:
            return {"error": f"No pude buscar playlists: {str(items)[:200]}"}
        out = []
        for it in (items or []):
            if not isinstance(it, dict):
                continue
            p = self._playlist_out(it)
            if p["url"]:
                out.append(p)
        if not out:
            return {"error": f"Sin playlists públicas para '{query}'"}
        return {"ok": True, "query": query, "results": out,
                "source": "YouTube Music (públicas, no tu biblioteca)"}

    async def tool_mymusic_playlist_tracks(self, playlist: str, limit: str = "25") -> Dict[str, Any]:
        pid = _parse_playlist_id(playlist)
        if not pid:
            return {"error": "Pasame el link o ID de la playlist (ej: tu Tranqui)"}
        try:
            n = max(1, min(int(str(limit or "25").strip()), 100))
        except (TypeError, ValueError):
            n = 25
        try:
            ok, pl = _try_each("get_playlist", pid, limit=n)
        except RuntimeError as e:
            return {"error": str(e)}
        if not ok:
            return {"error": f"No pude leer esa playlist: {str(pl)[:200]}"}
        pl = pl or {}
        tracks = []
        for it in (pl.get("tracks") or [])[:n]:
            t = self._track_out(it)
            if t["url"]:
                tracks.append(t)
        if not tracks:
            return {"error": "Esa playlist no tiene temas legibles"}
        return {"ok": True, "title": pl.get("title", "playlist"), "tracks": tracks}

    async def tool_mymusic_album(self, query: str) -> Dict[str, Any]:
        """Poner un ALBUM completo ('poné el álbum Homerun / un álbum de Paulo Londra').

        Busca álbumes/EPs, toma el primero y devuelve su playlist para Discord.
        """
        q = _clean_album_query(query or "")
        if not q:
            return {"error": "Indica qué álbum o de quién"}
        try:
            ok, items = _try_each("search", q, filter="albums", limit=5)
        except RuntimeError as e:
            return {"error": str(e)}
        if not ok or not items:
            return {"error": f"Sin álbumes para '{q}'"}
        pick = None
        for it in items:
            if isinstance(it, dict) and it.get("browseId"):
                if (it.get("type") or "").lower() == "album":
                    pick = it
                    break
                if pick is None:
                    pick = it
        if pick is None:
            return {"error": f"Sin álbumes para '{q}'"}
        try:
            ok2, alb = _try_each("get_album", pick["browseId"])
        except RuntimeError as e:
            return {"error": str(e)}
        if not ok2 or not isinstance(alb, dict):
            return {"error": f"No pude abrir el álbum: {str(alb)[:150]}"}
        pid = (alb.get("audioPlaylistId") or "").strip()
        if not pid:
            return {"error": "Ese álbum no tiene playlist reproducible"}
        artists = ", ".join(a.get("name", "") for a in (alb.get("artists") or []) if a.get("name"))
        return {"ok": True, "title": alb.get("title", pick.get("title", "álbum")),
                "artists": artists, "tracks": len(alb.get("tracks", [])),
                "url": f"https://music.youtube.com/playlist?list={pid}",
                "source": "álbum completo"}

    async def tool_mymusic_liked(self, limit: str = "20") -> Dict[str, Any]:
        try:
            n = max(1, min(int(str(limit or "20").strip()), 100))
        except (TypeError, ValueError):
            n = 20
        # PRIMERO tu memoria local (86 likeds de la API oficial).
        try:
            from core.mymusic_cache import load_cache
            cached = [t for t in (load_cache().get("liked") or [])
                      if isinstance(t, dict) and t.get("url")]
            if cached:
                return {"ok": True, "tracks": cached[:n],
                        "source": "tus likeds (memoria)"}
        except Exception:
            pass
        try:
            ok, data = _try_each("get_liked_songs", limit=n)
        except RuntimeError as e:
            return {"error": str(e)}
        if not ok:
            msg = str(data)
            if "twoColumnBrowseResultsRenderer" in msg or "None" in msg:
                return {"error": "Tus likeds no se pudieron leer con esta sesión. " + REFRESH_STEPS}
            return {"error": f"No pude leer tus likeds: {msg[:200]}"}
        items = (data.get("tracks", data) if isinstance(data, dict) else data) or {}
        out = []
        for it in (items or [])[:n]:
            if not isinstance(it, dict):
                continue
            t = self._track_out(it)
            if t["url"]:
                out.append(t)
        if not out:
            return {"error": "No encontre likeds legibles. " + REFRESH_STEPS}
        return {"ok": True, "tracks": out}

    def get_tools(self) -> List[ToolSchema]:
        return [
            ToolSchema(
                name="mymusic_playlists",
                description="Listar TUS playlists de YouTube Music (titulos con links). Si sale vacia, la cookie esta vencida (ver error).",
                parameters={
                    "limit": ToolParameter(type="string", description="Cantidad (1-50)"),
                },
            ),
            ToolSchema(
                name="mymusic_history",
                description="Ver lo ultimo que escuchaste (tus gustos recientes). Falla si la cookie esta vencida.",
                parameters={
                    "limit": ToolParameter(type="string", description="Cantidad (1-25)"),
                },
            ),
            ToolSchema(
                name="mymusic_search",
                description="PRIMERA OPCION para musica: busca en TU cuenta (ranking personalizado con tus gustos). RAMMA con doble M.",
                parameters={
                    "query": ToolParameter(type="string", description="Que buscar, ej RAMMA En una", required=True),
                },
            ),
            ToolSchema(
                name="mymusic_playlist_tracks",
                description="Leer los temas de UNA playlist (la Tranqui u otra): acepta link o ID. Ideal para 'poné mi Tranqui' o 'qué hay en X'.",
                parameters={
                    "playlist": ToolParameter(type="string", description="Link o ID de la playlist", required=True),
                    "limit": ToolParameter(type="string", description="Cantidad (1-100)"),
                },
            ),
            ToolSchema(
                name="mymusic_liked",
                description="Tus canciones con Me gusta de YouTube Music. Ideal para 'poné algo que me gusta / de mi biblioteca'.",
                parameters={
                    "limit": ToolParameter(type="string", description="Cantidad (1-100)"),
                },
            ),
            ToolSchema(
                name="mymusic_cached_play",
                description="PRIMERA OPCION para poner musica tuya: elige de tu cache local al instante (0 red). Para 'mi tranqui', 'algo mio', 'otra'.",
                parameters={
                    "source": ToolParameter(type="string", description="Playlist ('tranqui'), 'liked' o vacio"),
                    "artist": ToolParameter(type="string", description="Filtrar por artista (RAMMA, Paulo Londra)"),
                },
            ),
            ToolSchema(
                name="mymusic_recent",
                description="Tu 'volver a escuchar': lo ultimo que sono via CLAUDIO. Para 'poné lo de antes / lo que escuchaba / seguí con eso'.",
                parameters={
                    "limit": ToolParameter(type="string", description="Cantidad (1-30)"),
                },
            ),
            ToolSchema(
                name="search_playlists",
                description="Buscar playlists PUBLICAS de YouTube Music (NO tu biblioteca). Para 'busca una playlist de Milo J'. Si piden ponerla, se manda la primera a Discord.",
                parameters={
                    "query": ToolParameter(type="string", description="De quién o qué, ej: Milo J", required=True),
                },
            ),
            ToolSchema(
                name="mymusic_album",
                description="Poner un ALBUM completo ('el álbum Homerun', 'un álbum de Paulo Londra'). Devuelve la playlist del álbum para Discord.",
                parameters={
                    "query": ToolParameter(type="string", description="Álbum o artista, ej: Paulo Londra", required=True),
                },
            ),
            ToolSchema(
                name="mymusic_forgotten",
                description="Tus 'favoritos olvidados': likeds que hace rato no suenan. Para 'algo viejo / que no escucho hace rato'.",
                parameters={
                    "limit": ToolParameter(type="string", description="Cantidad (1-30)"),
                },
            ),
        ]

    def get_intents(self) -> List[IntentPattern]:
        return [
            IntentPattern(
                name="mymusic",
                patterns=[
                    r"mis playlists?",
                    r"mi m[uú]sica",
                    r"qu[eé] escucho",
                    r"mis gustos",
                    r"biblioteca.*m[uú]sica",
                    r"historial.*m[uú]sica",
                    r"que me gusta escuchar",
                    r"algo (que me gusta|m[ií]o|de mi biblioteca|de mis gustos)",
                    r"pon[eé].*(mi|mis|tranqui|likeds?|me gusta|biblioteca)",
                    r"(tranqui|likeds?).*(pon|reproduc|pasame|mostra)",
                    r"qu[eé] hay en (mi|la|esa).*playlist",
                    r"(volver a escuchar|lo de antes|lo que escuchaba|segu[ií] con eso|pon[eé] eso de nuevo)",
                    r"(favoritos olvidados|algo viejo|que no escucho hace)",
                    r"(busc[áa].*playlist|una playlist de|playlist de .+)",
                    r"(la|mi) playlist( de)? .+",
                    r"[áa]lbum(es)?( de| completo)?",
                ],
                entities=["query"],
            ),
        ]
