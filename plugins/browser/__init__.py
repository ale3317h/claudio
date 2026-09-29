import asyncio
import json
import os
import subprocess
from typing import Any, Dict, List
from urllib.parse import quote_plus

import httpx

from plugins.base import IntentPattern, Permission, PluginBase, PluginManifest, ToolParameter, ToolSchema

DEBUG_PORT = 9222


def _first_existing(paths) -> str:
    for p in paths:
        if p and os.path.exists(p):
            return p
    return ""


def _opera_exe() -> str:
    local = os.environ.get("LOCALAPPDATA", "")
    pf86 = os.environ.get("PROGRAMFILES(X86)", "")
    return _first_existing([
        os.path.join(local, "Programs", "Opera GX", "opera.exe"),
        os.path.join(local, "Programs", "Opera", "opera.exe"),
        os.path.join(pf86, "Opera", "opera.exe"),
    ])


def _debug_base() -> str:
    return f"http://127.0.0.1:{DEBUG_PORT}"


class Plugin(PluginBase):
    manifest = PluginManifest(
        name="browser",
        version="1.0.0",
        description="Control de Opera via Chrome DevTools Protocol",
        author="CLAUDIO",
        category="web",
        dependencies=[],
        permissions=[Permission.APP_LAUNCH, Permission.NETWORK],
    )

    async def initialize(self, core) -> None:
        self.core = core

    async def shutdown(self) -> None:
        pass

    def _profile_dir(self) -> str:
        from core.config import settings
        d = settings.memory.vault_path / "data" / "opera-profile"
        d.mkdir(parents=True, exist_ok=True)
        return str(d)

    async def _ensure_debugging(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                if (await client.get(f"{_debug_base()}/json/version")).status_code == 200:
                    return
        except Exception:
            pass
        exe = _opera_exe()
        if not exe:
            raise RuntimeError("No encontre Opera instalado")
        subprocess.Popen(  # noqa: ASYNC220 - lanzamiento fire-and-forget intencional
            [exe, f"--remote-debugging-port={DEBUG_PORT}",
             f"--user-data-dir={self._profile_dir()}", "--no-first-run"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, close_fds=True,
        )
        for _ in range(20):
            await asyncio.sleep(1.0)
            try:
                async with httpx.AsyncClient(timeout=3) as client:
                    if (await client.get(f"{_debug_base()}/json/version")).status_code == 200:
                        return
            except Exception:
                pass
        raise RuntimeError("Opera no responde en puerto debug (si ya estaba abierto, cerralo y reintenta)")

    def _to_url(self, text: str) -> str:
        t = (text or "").strip()
        if "://" in t:
            return t
        if " " not in t and "." in t:
            return "https://" + t
        return "https://www.google.com/search?q=" + quote_plus(t)

    async def _tabs(self) -> List[Dict[str, Any]]:
        await self._ensure_debugging()
        async with httpx.AsyncClient(timeout=10) as client:
            items = (await client.get(f"{_debug_base()}/json/list")).json()
        return [i for i in items if i.get("type") == "page"]

    async def tool_browser_open(self, url: str) -> Dict[str, Any]:
        await self._ensure_debugging()
        target = self._to_url(url)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                item = (await client.put(f"{_debug_base()}/json/new?{target}")).json()
        except Exception as e:
            return {"error": f"No pude abrir '{url}': {e}"}
        return {"ok": True, "id": item.get("id"), "title": item.get("title"), "url": item.get("url")}

    async def tool_browser_tabs(self) -> List[Dict[str, str]]:
        try:
            tabs = await self._tabs()
        except Exception as e:
            return [{"error": str(e)}]
        return [{"id": t.get("id", ""), "title": t.get("title", ""), "url": (t.get("url", "") or "")[:120]} for t in tabs]

    async def tool_browser_read(self, tab: str = "") -> Dict[str, Any]:
        try:
            tabs = await self._tabs()
        except Exception as e:
            return {"error": str(e)}
        if not tabs:
            return {"error": "Sin pestanas abiertas"}
        sel = tabs[0]
        if tab:
            needle = tab.strip().lower()
            for t in tabs:
                if (needle in (t.get("id") or "").lower()
                        or needle in (t.get("title") or "").lower()
                        or needle in (t.get("url") or "").lower()):
                    sel = t
                    break
        ws_url = sel.get("webSocketDebuggerUrl")
        if not ws_url:
            return {"error": "Esa pestana no expone canal debug"}
        import websockets
        try:
            async with websockets.connect(ws_url, max_size=10 * 1024 * 1024) as ws:
                await ws.send(json.dumps({
                    "id": 1, "method": "Runtime.evaluate",
                    "params": {"expression": "(document.body ? document.body.innerText : '').slice(0, 8000)",
                               "returnByValue": True},
                }))
                raw = await asyncio.wait_for(ws.recv(), timeout=15)
            val = json.loads(raw).get("result", {}).get("result", {}).get("value", "")
        except Exception as e:
            return {"error": f"No pude leer la pestana: {e}"}
        return {"ok": True, "title": sel.get("title"), "url": sel.get("url"), "text": (val or "")[:8000]}

    def get_tools(self) -> List[ToolSchema]:
        return [
            ToolSchema(
                name="browser_open",
                description="Abrir una URL o busqueda en Opera (crea pestana). Acepta URL, dominio o texto a googlear.",
                parameters={
                    "url": ToolParameter(type="string", description="URL, dominio o texto a buscar", required=True),
                },
            ),
            ToolSchema(
                name="browser_tabs",
                description="Listar pestanas abiertas en Opera (id, titulo, url). NO interrumpe.",
                parameters={},
            ),
            ToolSchema(
                name="browser_read",
                description="Leer el texto de una pestana de Opera (por id, titulo o url; vacio = actual). NO interrumpe.",
                parameters={
                    "tab": ToolParameter(type="string", description="Id, titulo o url parcial de la pestana"),
                },
            ),
        ]

    def get_intents(self) -> List[IntentPattern]:
        return [
            IntentPattern(
                name="browser_open",
                patterns=[
                    r"abr[ií].*(youtube|video|pagina|p[aá]gina|web|sitio|link|navegador|opera|google|pestana)",
                    r"naveg[aoe].*",
                    r"busc[aoe].*en (google|internet|la web)",
                    r"anda a .*",
                    r"youtube.*",
                ],
                entities=["url"],
            ),
            IntentPattern(
                name="browser_read",
                patterns=[
                    r"(que|qué) dice.*(pagina|p[aá]gina|pestana|web)",
                    r"le[eé].*(pagina|p[aá]gina|pestana)",
                    r"resum.*(pagina|p[aá]gina|pestana|web)",
                ],
                entities=["tab"],
            ),
        ]
