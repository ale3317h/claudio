import os
from typing import Any, Dict, List

from dotenv import load_dotenv

from plugins.base import IntentPattern, Permission, PluginBase, PluginManifest, ToolParameter, ToolSchema

load_dotenv()

TAVILY_URL = "https://api.tavily.com/search"
DUCK_URL = "https://html.duckduckgo.com/html/"
DUCK_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def _strip_tags(s: str) -> str:
    import html as _html
    import re as _re
    return _html.unescape(_re.sub(r"<[^>]+>", "", s or "")).strip()


def _tavily_keys() -> list:
    """Primaria + backup (coma-separadas): si una muere/cuota, sigue la otra."""
    keys = []
    primary = os.getenv("TAVILY_API_KEY", "").strip()
    if primary:
        keys.append(primary)
    for k in (os.getenv("TAVILY_API_KEYS", "") or "").split(","):
        k = k.strip()
        if k and k not in keys:
            keys.append(k)
    return keys


async def _search_duck(query: str, n: int):
    """Busqueda GRATIS sin key (DuckDuckGo HTML, solo stdlib+httpx).

    Frágil por diseño (si DDG cambia el HTML, falla): el llamante cae a error
    claro. No gasta cuota Tavily.
    """
    import re as _re
    from urllib.parse import parse_qs, unquote, urlparse

    import httpx
    try:
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": DUCK_UA}) as client:
            r = await client.get(DUCK_URL, params={"q": query})
    except Exception as e:
        return {"error": f"DuckDuckGo sin conexión: {str(e)[:120]}"}
    if r.status_code != 200 or "anomaly-modal" in r.text:
        return {"error": f"DuckDuckGo bloqueó ({r.status_code}): probá con Tavily"}
    links = _re.findall(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                        r.text, flags=_re.DOTALL)
    snips = _re.findall(r'class="result__snippet"[^>]*>(.*?)</a>',
                        r.text, flags=_re.DOTALL)
    out = []
    for i, (href, raw_title) in enumerate(links):
        if len(out) >= n:
            break
        url = href.strip()
        if url.startswith("/"):
            try:
                q = parse_qs(urlparse("https:" + url if url.startswith("//") else url).query)
                if q.get("uddg"):
                    url = unquote(q["uddg"][0])
                elif url.startswith("//"):
                    url = "https:" + url
            except Exception:
                pass
        if not url.startswith("http"):
            continue
        snip = _strip_tags(snips[i]) if i < len(snips) else ""
        out.append({"title": _strip_tags(raw_title) or "sin titulo",
                    "url": url, "snippet": snip[:400]})
    if not out:
        return {"error": f"Sin resultados gratis para '{query}' (probá con Tavily)"}
    return {"ok": True, "query": query, "results": out, "source": "duckduckgo"}


def _clean_query(text: str) -> str:
    """Query limpia: sin scaffolding de compuestas ('primero X y despues...')."""
    try:
        from plugins.ytmusic import _strip_compound_scaffold
        text = _strip_compound_scaffold(text or "")
    except Exception:
        pass
    import re as _re
    q = _re.sub(r"\bbusc[áa](me)?\b|\bbuscando\b|\ben\s+(google|internet|la\s+web|el\s+navegador)\b"
               r"|\bgoogle[ae]?\b|\bpor\s+favor\b|\b che\b", " ", text or "", flags=_re.IGNORECASE)
    return " ".join(q.split()).strip(" ,.!?¿¡\"'")


class Plugin(PluginBase):
    manifest = PluginManifest(
        name="web_search",
        version="1.0.0",
        description="Búsqueda web (Tavily): datos actuales que CLAUDIO no tiene",
        author="CLAUDIO",
        category="web",
        dependencies=[],
        permissions=[Permission.NETWORK],
    )

    async def initialize(self, core) -> None:
        self.core = core

    async def shutdown(self) -> None:
        pass

    async def tool_web_search(self, query: str, max_results: str = "3",
                              time_range: str = "") -> Dict[str, Any]:
        import httpx
        q = _clean_query(query)
        if len(q) < 3:
            return {"error": "Indica qué buscar en la web"}
        try:
            n = max(1, min(int(str(max_results or "3").strip()), 5))
        except (TypeError, ValueError):
            n = 3
        keys = _tavily_keys()
        if not keys:
            # Sin key: via gratis (DuckDuckGo). Sin cuota, menos profunda.
            return await _search_duck(q, n)
        last_err = ""
        data = None
        tr = (time_range or "").strip().lower()
        if tr not in ("day", "week", "month", "year"):
            tr = ""
        if tr:
            # Fecha pasada explícita ("2018"): el filtro recency mataría el
            # contenido histórico. Solo vale para presente (hoy/ayer/semana).
            import datetime as _dt
            import re as _re
            m = _re.search(r"\b(19|20)\d{2}\b", q)
            if m and int(m.group(0)) < _dt.datetime.now().year:
                tr = ""
        payload = {"query": q, "search_depth": "advanced",
                   "max_results": n, "include_answer": True,
                   "include_raw_content": False}
        if tr:
            payload["time_range"] = tr  # "hoy"/resultados: recency real, no basura vieja
        for api_key in keys:
            try:
                async with httpx.AsyncClient(timeout=20) as client:
                    r = await client.post(TAVILY_URL, json={"api_key": api_key, **payload})
            except Exception as e:
                last_err = f"Sin conexión a Tavily: {str(e)[:150]}"
                continue  # red caída: probar siguiente key igual
            if r.status_code in (401, 403):
                last_err = "Tavily rechazó la key (401/403)"
                continue  # key muerta: siguiente
            if r.status_code == 429:
                last_err = "Cuota Tavily agotada en esta key"
                continue  # sin cupo: siguiente
            if r.status_code != 200:
                last_err = f"Tavily devolvió {r.status_code}: {r.text[:150]}"
                break  # error raro: no insistir
            try:
                data = r.json()
            except Exception:
                data = None
                last_err = "Respuesta inválida de Tavily"
            break
        if not isinstance(data, dict):
            return {"error": last_err or "Sin keys Tavily"}
        out = []
        for it in (data.get("results") or [])[:n]:
            if not isinstance(it, dict):
                continue
            out.append({"title": it.get("title", "sin titulo"),
                        "url": it.get("url", ""),
                        "snippet": (it.get("content", "") or "")[:400]})
        if not out and not data.get("answer"):
            return {"error": f"Sin resultados web para '{q}'"}
        res: Dict[str, Any] = {"ok": True, "query": q, "results": out, "source": "tavily"}
        if data.get("answer"):
            res["answer"] = str(data["answer"])[:600]
        return res

    def get_tools(self) -> List[ToolSchema]:
        return [
            ToolSchema(
                name="web_search",
                description="Buscar en la WEB (noticias, datos actuales, lo que no está en tu biblioteca). Con TAVILY_API_KEY usa Tavily (profundo); sin key usa DuckDuckGo gratis. Query limpia y corta. NO usar para música.",
                parameters={
                    "query": ToolParameter(type="string", description="Qué buscar, ej: resultado Boca River", required=True),
                    "max_results": ToolParameter(type="string", description="Resultados (1-5, default 3)"),
                    "time_range": ToolParameter(type="string", description="Recencia: day/week/month/year (para 'hoy', partidos, noticias)"),
                },
            ),
        ]

    def get_intents(self) -> List[IntentPattern]:
        return [
            IntentPattern(
                name="web_search",
                patterns=[
                    r"busc[áa].*(google|internet|web|navegador)",
                    r"(google|internet).*(busc|qué|que|quién|quien|cuándo|cuando|dónde|donde)",
                    r"qu[eé] est[aá] pasando",
                    r"[úu]ltimas? noticias",
                    r"noticias de",
                    r"[úu]ltimo partido|resultado del partido|cu[aá]nto sali[oó]",
                    r"qui[eé]n es .+",
                    r"qu[eé] es .+",
                    r"cu[áa]ndo (es|fue|juega|sale)",
                    r"d[oó]nde queda",
                    r"a cu[áa]nto est[aá]",
                    r"clima en|pron[oó]stico",
                ],
                entities=["query"],
            ),
        ]
