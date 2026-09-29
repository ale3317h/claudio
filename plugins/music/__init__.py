import json
import re
import time
from typing import Any, Dict, List

from plugins.base import IntentPattern, Permission, PluginBase, PluginManifest, ToolParameter, ToolSchema

RAMMA_CANON = "RAMMA"
_RAMA_RE = re.compile(r"\bram+a\b|\bcarb[yi]\b|\bramba\b", re.IGNORECASE)
_SKILLET_RE = re.compile(
    r"\bskijet\b|\bskyjet\b|\bskyshet\b|\bskilet\b|\bskillets?\b"
    r"|\bsquilet\b|\besquilet\b|\beskillet\b|\bskidget\b|\bskidjet\b"
    r"|\bkitschets?\b|\bkitshets?\b|\bkischets?\b",
    re.IGNORECASE,
)
_RISE_RE = re.compile(r"\braze\b|\braice\b|\brai\b", re.IGNORECASE)
_PAULO_RE = re.compile(
    r"\bpa[bu]l[ao]\s+londra\b|\bpobl[ao]\s+londr?a\b"
    r"|\bpobl[ao]dondra\b|\bpoblolondra\b|\bpobl[ao]\s+(?:de\s+)?londra\b",
    re.IGNORECASE,
)
# "r b r" / "rb" / "rv" dictado por voz = álbum RV de RAMMA
_RBR_RE = re.compile(r"\b(r\s*,?\s*b\s*,?\s*r|rb|rv)\b", re.IGNORECASE)
_VAGUE_RE = re.compile(
    r"^(esa|ese|esta|este|eso|esa musica|esa música|ese tema|esa cancion|esa canción|pon esa|ponelo|ponela|dale|esa misma)$",
    re.IGNORECASE,
)


PAULO_CANON = "Paulo Londra"


def canon_artist(name: str) -> str:
    """RAMMA siempre con doble M mayúscula, aunque el STT traiga 'rama'."""
    n = (name or "").strip()
    if not n:
        return n
    if _RAMA_RE.search(n):
        return RAMMA_CANON
    if _PAULO_RE.search(n):
        return PAULO_CANON
    return n


def canon_query(query: str, artist: str = "") -> str:
    """Normaliza la búsqueda para no caer en 'rama' (dios hindú) ni en un tema por defecto."""
    q = _PAULO_RE.sub(PAULO_CANON, (query or "").strip())
    q = re.sub(r"\b(saga|aga|saja)\s+boys?\b|\bsaja\s+b\b", "Saja Boys", q, flags=re.IGNORECASE)
    q = re.sub(r"\b(vichero|vichera|viyero|viyera|biyero|biyera|villegru|vilegru)\b", "villero", q, flags=re.IGNORECASE)
    q = re.sub(r"\b(gonna|gona|wana|wanna|yana)\s+(fly|flight|split|flay)\s+now\b",
               "Gonna Fly Now", q, flags=re.IGNORECASE)
    q = re.sub(r"\brocky\b|\bracky\b|\braky\b|\broky\b|\broki\b|\brucky\b"
               r"|\brackynolds\b|\brockynolds\b",
               "Gonna Fly Now", q, flags=re.IGNORECASE)
    q = re.sub(r"\boro\s+beso\b", "Otro beso", q, flags=re.IGNORECASE)
    q = re.sub(
        r"\bpero\s+no\s+(?:en\s+vivo|ac[úu]stico|en\s+directo|remix|cover|live|en\s+espa[ñn]ol|espa[ñn]ol)\b"
        r"|\b(?:no|sin)\s+(?:en\s+vivo|ac[úu]stico|en\s+directo|remix|cover|live)\b"
        r"|\bque\s+no\s+sea\s+(?:en\s+vivo|ac[úu]stico|en\s+directo|remix|cover|live|[a-záéíóúñ]{3,}(?:\s+[a-záéíóúñ]{2,}){0,2})\b",
        " ", q, flags=re.IGNORECASE)
    q = " ".join(q.split()).strip(" ,.!?¿¡\"'")
    q = _SKILLET_RE.sub("Skillet", q)
    q = _RISE_RE.sub("Rise", q)
    a = canon_artist(artist or "")
    # Si el artista es RAMMA pero el query vino sin él, agregarlo.
    if a == RAMMA_CANON and not _RAMA_RE.search(q):
        q = f"{RAMMA_CANON} {q}".strip() if q and not _VAGUE_RE.match(q) else RAMMA_CANON
    else:
        q = _RAMA_RE.sub(RAMMA_CANON, q)
    # "r, b, r" dictado = álbum RV de RAMMA
    if _RBR_RE.search(q) and RAMMA_CANON not in q:
        q = f"{RAMMA_CANON} RV"
    elif _RBR_RE.search(q):
        q = _RBR_RE.sub("RV", q)
    q = " ".join(q.split())
    # "RAMMA" solo es ambiguo en YouTube: desambiguar hacia el trapero argentino.
    if q.strip().upper() == RAMMA_CANON:
        q = "RAMMA cantante argentino trap"
    return q.strip() or RAMMA_CANON


def _is_playable(url: str) -> bool:
    u = (url or "").lower()
    return "watch?v=" in u or "youtu.be/" in u


def _watch_url(entry: Dict[str, Any]) -> str:
    url = (entry.get("webpage_url") or entry.get("url") or "").strip()
    if url.startswith("http"):
        return url
    vid = (entry.get("id") or "").strip()
    if vid and len(vid) == 11:
        return f"https://www.youtube.com/watch?v={vid}"
    return url


class Plugin(PluginBase):
    manifest = PluginManifest(
        name="music",
        version="1.1.0",
        description="Buscar musica en YouTube, evitar repetidos y guardar artistas",
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

    CACHE_TTL = 7 * 24 * 3600

    def _cache_get(self, query: str):
        try:
            store = getattr(self, "store", None)
            if store is None:
                return None
            raw = store.recall(f"musiccache:{query.strip().lower()}")
            if not raw:
                return None
            data = json.loads(raw)
            if time.time() - data.get("ts", 0) > self.CACHE_TTL:
                return None
            if isinstance(data.get("results"), list):
                return data["results"]
        except Exception:
            pass
        return None

    def _cache_put(self, query: str, results: list) -> None:
        try:
            store = getattr(self, "store", None)
            if store is None:
                return
            store.remember(
                f"musiccache:{query.strip().lower()}",
                json.dumps({"ts": time.time(), "results": results}, ensure_ascii=False),
            )
        except Exception:
            pass

    def _search(self, query: str, n: int = 5) -> List[Dict[str, str]]:
        cached = self._cache_get(query)
        if cached is not None:
            return cached[:n]
        from yt_dlp import YoutubeDL
        opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": "in_playlist",
            "noplaylist": True,
        }
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch{n}:{query}", download=False)
        results = []
        for entry in (info or {}).get("entries", []) or []:
            if not isinstance(entry, dict):
                continue
            url = _watch_url(entry)
            if url.startswith("http"):
                results.append({"title": entry.get("title", "sin titulo"), "url": url})
            if len(results) >= n:
                break
        self._cache_put(query, results)
        return results

    def _played_urls(self, limit: int = 20) -> set:
        urls = set()
        try:
            for event in self.store.recent_events(kind="musica", limit=limit):
                try:
                    data = json.loads(event.get("content", ""))
                    if isinstance(data, dict) and data.get("url"):
                        urls.add(data["url"])
                except Exception:
                    pass
        except Exception:
            pass
        return urls

    def _last_played(self):
        """Ultimo tema puesto (ordenado, no set). None si no hay historial."""
        import structlog
        logger = structlog.get_logger(__name__)
        try:
            for event in self.store.recent_events(kind="musica", limit=10):
                try:
                    data = json.loads(event.get("content", ""))
                    if isinstance(data, dict) and data.get("url"):
                        return {"title": data.get("title", "el tema anterior"),
                                "url": data["url"]}
                except Exception as e:
                    logger.debug("replay_event_saltado", error=str(e)[:100])
                    continue
        except Exception as e:
            logger.debug("replay_historial_fallo", error=str(e)[:100])
        return None

    async def tool_music_replay(self) -> Dict[str, Any]:
        """Repite LA ANTERIOR ('de vuelta / de nuevo / otra vez / repetí').

        No busca nada: devuelve el ultimo link puesto. Exacto por definicion.
        """
        pick = self._last_played()
        if not pick:
            return {"error": "Todavía no sonó nada: pedime un tema o artista"}
        return {"ok": True, "pick": pick}

    async def tool_music_wait(self, segundos: str = "5") -> Dict[str, Any]:
        """Espera N segundos antes del paso siguiente ('después de 5 segundos...').

        Para pedidos compuestos con pausa temporal. Tope 300s.
        """
        import asyncio
        try:
            n = max(1, min(300, int(str(segundos or "5").strip())))
        except (TypeError, ValueError):
            n = 5
        await asyncio.sleep(n)
        return {"ok": True, "segundos": n}

    async def tool_music_search(self, query: str, max_results: int = 3, artist: str = "") -> Dict[str, Any]:
        raw = (query or "").strip()
        if not raw and not (artist or "").strip():
            return {"error": "Indica que tema, artista o video buscar"}
        query = canon_query(raw or artist, artist)
        try:
            n = int(str(max_results).strip())
        except (TypeError, ValueError):
            n = 3
        n = max(1, min(n, 5))
        try:
            from yt_dlp import YoutubeDL  # noqa: F401 (verifica dependencia)
            results = self._search(query, n)
        except ImportError:
            return {"error": "Falta yt-dlp: pip install yt-dlp"}
        except Exception as e:
            return {"error": f"No pude buscar en YouTube: {str(e)[:200]}"}
        if not results:
            return {"error": f"Sin resultados para '{query}'"}
        return {"ok": True, "query": query, "results": results}

    async def tool_music_play(self, query: str, artist: str = "") -> Dict[str, Any]:
        raw = (query or "").strip()
        artist = canon_artist(artist or "")
        if not raw and not artist:
            return {"error": "Indica que tema o artista reproducir"}
        # "esa musica" sin contexto no puede adivinarse: si vino vago y sin artista,
        # se asume RAMMA (artista favorito) en vez del sesgo viejo a "Algun dia".
        if not raw or _VAGUE_RE.match(raw):
            query = canon_query(artist or RAMMA_CANON, artist or RAMMA_CANON)
            if not artist:
                artist = RAMMA_CANON
        else:
            query = canon_query(raw, artist)
            # Si el query menciona rama/RAMMA, el artista queda fijado para no perderlo.
            if _RAMA_RE.search(raw) or RAMMA_CANON in query:
                artist = artist or RAMMA_CANON
        try:
            results = self._search(query, 5)
        except ImportError:
            return {"error": "Falta yt-dlp: pip install yt-dlp"}
        except Exception as e:
            return {"error": f"No pude buscar en YouTube: {str(e)[:200]}"}
        if not results:
            return {"error": f"Sin resultados para '{query}'"}
        played = self._played_urls()
        import unicodedata as _ud

        def _sk(s):
            t = "".join(c for c in _ud.normalize("NFD", (s or "").lower())
                        if _ud.category(c) != "Mn")
            return re.sub(r"[^bcdfghjklmnpqrstvwxyz]", "", t)

        _VERBS = {"pongas", "ponga", "pongan", "pongo", "pone", "ponen", "reproduce",
                  "reproduci", "reproducir", "suena", "suene", "suenan", "pasa", "pasan"}
        excluded = []
        patterns = (
            r"\bno\s+(?:pongas|ponga|pongan|pongo|reproduzcas|reproduzca|suene|suena)\s+"
            r"(?:la\s+de\s+|el\s+de\s+|la\s+|el\s+)?([A-Za-zÁ-ÿ]{3,}(?:\s+[A-Za-zÁ-ÿ]{2,}){0,2})",
            r"\bno\s+(?:la\s+de\s+|el\s+de\s+|la\s+|el\s+)?([A-Za-zÁ-ÿ]{4,}(?:\s+[A-Za-zÁ-ÿ]{2,}){0,2})",
            r"\bque\s+no\s+sea\s+([A-Za-zÁ-ÿ]{3,}(?:\s+[A-Za-zÁ-ÿ]{2,}){0,2})",
            r"\bmenos\s+([A-Za-zÁ-ÿ]{4,}(?:\s+[A-Za-zÁ-ÿ]{2,}){0,2})",
        )
        for rx in patterns:
            for m in re.finditer(rx, raw, re.IGNORECASE):
                w = " ".join(m.group(1).split())
                if len(_sk(w)) >= 3 and w.split()[0].lower() not in _VERBS \
                        and w.lower() not in [x.lower() for x in excluded]:
                    excluded.append(w)
        qsk = _sk(query)
        blocked = [k for k in ("lgnd",) if k not in qsk]  # vetados salvo pedido explícito

        def _ok(r):
            if not _is_playable(r["url"]):
                return False
            sk = _sk(r.get("title", ""))
            if any(b in sk for b in blocked):
                return False
            return not any(len(_sk(w)) >= 3 and _sk(w) in sk for w in excluded)

        playable = [r for r in results if _ok(r)]
        if not playable:
            if [r for r in results if _is_playable(r["url"])]:
                # Hay links pero todos son lo vetado/excluido: avisar, no mandar.
                return {"error": "Todo lo que encuentro es lo que me pediste evitar. ¿Te busco otro tema?"}
            return {"error": f"Solo encontré canales o perfiles para '{query}', pedime un tema puntual"}
        pick = next((r for r in playable if r["url"] not in played), playable[0])
        try:
            self.store.log_event("musica", json.dumps(pick, ensure_ascii=False))
        except Exception:
            pass
        name = (artist or "").strip()
        if name:
            try:
                self.store.remember(f"artista_{name.lower()}", pick["title"])
            except Exception:
                pass
        return {"ok": True, "query": query, "pick": pick, "options": results[1:3]}

    def get_tools(self) -> List[ToolSchema]:
        return [
            ToolSchema(
                name="music_search",
                description="SOLO para mostrar opciones al usuario. PROHIBIDO usar para reproducir: para poner musica usa music_play. RAMMA va con doble M.",
                parameters={
                    "query": ToolParameter(type="string", description="Que buscar, ej: RAMMA En una, soda stereo nada personal", required=True),
                    "max_results": ToolParameter(type="string", description="Cantidad de resultados (1-5)"),
                    "artist": ToolParameter(type="string", description="Si nombraron RAMMA, pasa artist=RAMMA"),
                },
            ),
            ToolSchema(
                name="music_replay",
                description="Repetir LA ANTERIOR ('de vuelta/de nuevo/otra vez'): devuelve el ultimo link sin buscar. Usar SIEMPRE para eso.",
                parameters={},
            ),
            ToolSchema(
                name="music_play",
                description="Elegir que reproducir: busca, salta lo ya sonado y guarda el artista. USAR para poner musica. Si piden RAMMA sin tema puntual, query=RAMMA y artist=RAMMA (NO asumir 'Algun dia').",
                parameters={
                    "query": ToolParameter(type="string", description="Tema o artista (RAMMA con doble M)", required=True),
                    "artist": ToolParameter(type="string", description="Cantante si lo nombraron, ej RAMMA (para guardarlo y variar)"),
                },
            ),
        ]

    def get_intents(self) -> List[IntentPattern]:
        return [
            IntentPattern(
                name="music_search",
                patterns=[
                    r"pon.*(musica|música|tema|canci[oó]n|rock|video)",
                    r"reproduc.*(musica|música|tema|canci[oó]n|video|algo)",
                    r"busc.*(musica|música|tema|canci[oó]n|video).*",
                    r"pasame.*link.*",
                    r"poneme.*",
                    r"otra (canci[oó]n|tema| de)",
                ],
                entities=["query"],
            ),
        ]
