import asyncio
import os
import re
from typing import Any, Dict, List

import httpx
from dotenv import load_dotenv

from plugins.base import IntentPattern, Permission, PluginBase, PluginManifest, ToolParameter, ToolSchema

load_dotenv()

_BARE_BANG_CMD_RE = re.compile(
    r"(^|[\s\"'¿¡(\[])[!¡]+(play|p|pause|resume|unpause|skip|queue|stop|clear)\b",
    re.IGNORECASE,
)
_M_SPACE_CMD_RE = re.compile(r"\bm\s+(play|p|pause|resume|skip|queue|stop)\b", re.IGNORECASE)
_BARE_WORD_CMD_RE = re.compile(
    r"^(play|p|pause|resume|unpause|skip|queue|stop|clear)\b(.*)$",
    re.IGNORECASE,
)


# Reto/correccion del usuario hacia CLAUDIO: jamas se postea en Discord.
# (Una vez se mando "Mentira, no lo hiciste..." al canal por un intent flojo.)
_CORRECTION_RE = re.compile(
    r"\bmentira\b|\bno\s+lo\s+hiciste\b|\bno\s+hiciste\b|\bte\s+equivocaste\b"
    r"|\best[áa]\s+mal\b|\beso\s+no\b|\bnada\s+que\s+ver\b|\bno\s+es\s+eso\b",
    re.IGNORECASE,
)
_SEND_OK_RE = re.compile(
    r"\b(mand[áa]|envi[áa]|escrib|public[áa]|decile|contale|avisale)\b|^m!\w+|https?://",
    re.IGNORECASE,
)


def fix_discord_command(text: str) -> str:
    """Jockie SOLO obedece comandos con m al principio (m!play, m!pause...).

    Corrige los olvidos típicos del LLM/STT: !play -> m!play, play X -> m!play X.
    Deja intacto cualquier texto que no sea comando.
    """
    t = (text or "").strip()
    if not t:
        return t
    # "!play X" / "¡pause" -> "m!play X" / "m!pause"
    t = _BARE_BANG_CMD_RE.sub(lambda m: f"{m.group(1)}m!{m.group(2)}", f" {t}").strip()
    t = _M_SPACE_CMD_RE.sub(lambda m: f"m!{m.group(1)}", t)
    # Solo si TODO el mensaje es un comando sin prefijo ("play <link>", "pause", ...)
    # se agrega la m. No toca frases normales como "play es una palabra".
    # Heurística: es comando si empieza con verbo conocido y (es una sola palabra
    # de control o lo sigue un link / pocas palabras de comando).
    m = _BARE_WORD_CMD_RE.match(t)
    if m:
        verb = m.group(1).lower()
        rest = (m.group(2) or "").strip()
        # Palabras de control solas siempre son comandos (pause, resume, skip...).
        # play/p seguidos de link o texto corto también.
        # (No frases largas: "play significa jugar porque...".)
        if not t.lower().startswith("m!") and (
            verb in ("pause", "resume", "unpause", "skip", "queue", "stop", "clear")
            or (verb in ("play", "p") and rest
                and (rest.startswith("http") or len(rest.split()) <= 12))
        ):
            t = f"m!{verb} {rest}".strip()
    # Alias que usa la memoria: m!play -> m!p
    t = re.sub(r"^m!play\b", "m!p", t, flags=re.IGNORECASE)
    return " ".join(t.split())


class Plugin(PluginBase):
    manifest = PluginManifest(
        name="discord",
        version="1.0.0",
        description="Enviar mensajes a canales de Discord via webhook",
        author="CLAUDIO",
        category="communication",
        dependencies=[],
        permissions=[Permission.NETWORK],
    )

    async def initialize(self, core) -> None:
        try:
            from core.config import settings
            from core.store import Store
            self.store = Store(settings.memory.vault_path / "data")
        except Exception:
            self.store = None
        self.core = core

    async def shutdown(self) -> None:
        pass

    async def tool_music_control(self, action: str = "") -> Dict[str, Any]:
        """Pausa/reanuda/salta/frena la música de Discord (Jockie). SOLO Discord.

        No toca YouTube, Spotify ni nada local: esos se manejan en sus apps.
        Rápido, sin buscar nada. action: pause | resume | skip | stop.
        stop = m!stop (Jockie frena y limpia la cola solo, sin m!clear extra).
        """
        action = (action or "").strip().lower()
        if action not in ("pause", "resume", "skip", "stop"):
            return {"error": "Acción invalida: pause, resume, skip o stop"}
        discord_cmd = {"pause": "m!pause", "resume": "m!resume",
                       "skip": "m!skip", "stop": "m!stop"}[action]
        r = await self.tool_discord_user_send(message=discord_cmd)
        if "error" in r:
            return {"error": f"No pude frenar Discord: {r['error'][:150]}"}
        return {"ok": True, "action": action, "command": discord_cmd,
                "source": "discord", "via": ["discord"], "cleared": False}

    def _webhook(self, channel: str = "") -> str:
        if channel:
            url = os.getenv(f"DISCORD_WEBHOOK_{channel.strip().upper()}", "")
            if url:
                return url
        url = os.getenv("DISCORD_WEBHOOK", "")
        if not url:
            raise RuntimeError(
                "Falta DISCORD_WEBHOOK en .env. Crealo en Discord: "
                "Ajustes del servidor > Integraciones > Webhooks > Nuevo webhook > Copiar URL"
            )
        return url

    async def tool_discord_send(
        self, message: str = "", content: str = "", channel: str = "", username: str = "CLAUDIO"
    ) -> Dict[str, Any]:
        text = (message or content or "").strip()
        if not text:
            return {"error": "Mensaje vacio: indica 'message' con el texto a enviar"}
        if _CORRECTION_RE.search(text) and not _SEND_OK_RE.search(text):
            return {"error": "Eso parece una corrección hacia CLAUDIO, no un mensaje para Discord: no lo mando"}
        try:
            url = self._webhook(channel)
        except RuntimeError as e:
            return {"error": str(e)}
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(
                    url,
                    json={"content": text[:2000], "username": username or "CLAUDIO"},
                )
        except Exception as e:
            return {"error": f"No pude contactar Discord: {e}"}
        if r.status_code not in (200, 204):
            return {"error": f"Discord respondio {r.status_code}: {r.text[:200]}"}
        return {"ok": True, "channel": channel or "default"}

    async def tool_discord_user_send(
        self, message: str = "", content: str = "", channel_id: str = ""
    ) -> Dict[str, Any]:
        token = os.getenv("DISCORD_USER_TOKEN", "").strip()
        if not token:
            return {"error": "Falta DISCORD_USER_TOKEN en .env"}
        text = fix_discord_command((message or content or "").strip())
        if not text:
            return {"error": "Mensaje vacio"}
        # El LLM a veces inventa el ID ("ID_DEL_CANAL..."): solo vale un snowflake
        # numerico; cualquier otra cosa se ignora y se usa el .env (canal real).
        cid = (channel_id or "").strip()
        if not cid.isdigit():
            cid = os.getenv("DISCORD_CHANNEL_ID", "").strip()
        if not cid.isdigit():
            return {"error": "Falta DISCORD_CHANNEL_ID numerico en .env (click derecho al canal > Copiar ID, con modo desarrollador)"}
        headers = {
            "Authorization": token,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(
                    f"https://discord.com/api/v9/channels/{cid}/messages",
                    headers=headers,
                    json={"content": text[:2000]},
                )
        except Exception as e:
            return {"error": f"No pude contactar Discord: {e}"}
        if r.status_code not in (200, 201):
            return {"error": f"Discord respondio {r.status_code}: {r.text[:200]}"}
        return {"ok": True, "via": "user-token", "channel_id": cid}

    async def tool_discord_command(self, command: str, window: str = "Discord") -> Dict[str, Any]:
        core = getattr(self, "core", None)
        pc = core.plugins.get("pc_control") if core is not None else None
        if pc is None:
            return {"error": "Se necesita el plugin pc_control instalado y cargado"}
        text = fix_discord_command((command or "").strip())
        if not text:
            return {"error": "Comando vacio"}
        r = await pc.execute_tool("focus_window", {"title_contains": window})
        if not r.get("ok"):
            await pc.execute_tool("launch_app", {"app_name": "discord"})
            await asyncio.sleep(2.0)
            r = await pc.execute_tool("focus_window", {"title_contains": window})
            if not r.get("ok"):
                return {"error": f"No pude enfocar Discord: {r.get('error', r)}"}
        await asyncio.sleep(1.0)
        r = await pc.execute_tool("type_text", {"text": text})
        if not r.get("ok"):
            return {"error": f"No pude escribir: {r.get('error', r)}"}
        await asyncio.sleep(0.3)
        r = await pc.execute_tool("press_hotkey", {"keys": "enter"})
        if not r.get("ok"):
            return {"error": f"No pude enviar: {r.get('error', r)}"}
        return {"ok": True, "command": text, "via": "ui"}

    def get_tools(self) -> List[ToolSchema]:
        return [
            ToolSchema(
                name="discord_send",
                description="Enviar un mensaje de texto a un canal de Discord via webhook. NO interrumpe: no roba foco ni teclado. Los bots lo ignoran.",
                parameters={
                    "message": ToolParameter(type="string", description="Texto del mensaje", required=True),
                    "channel": ToolParameter(type="string", description="Nombre del canal configurado (opcional, usa el webhook por defecto si se omite)"),
                    "username": ToolParameter(type="string", description="Nombre visible del remitente (por defecto CLAUDIO)"),
                },
            ),
            ToolSchema(
                name="discord_command",
                description="USAR ESTA HERRAMIENTA (y no launch_app/focus_window sueltos) cuando el pedido mencione Discord junto a: reproducir, poner musica, pausar, comando de bot, m!. El comando SIEMPRE lleva m al principio (m!play/m!p, m!pause, m!resume, m!skip, m!queue, m!stop). PROHIBIDO mandar !play/!pause sin la m. INTERRUMPE: toma foco y teclado unos segundos.",
                parameters={
                    "command": ToolParameter(type="string", description="Comando exacto con m!, ej: m!pause, m!p https://...", required=True),
                    "window": ToolParameter(type="string", description="Parte del titulo de la ventana de Discord"),
                },
            ),
            ToolSchema(
                name="discord_user_send",
                description="Enviar mensaje COMO EL USUARIO via API (self-bot). Via SILENCIOSA y preferida para 'en segundo plano': no roba foco y los bots la obedecen. El mensaje SIEMPRE lleva m al principio si es comando (m!play/m!p, m!pause, m!resume, m!skip, m!queue, m!stop). PROHIBIDO mandar !play sin la m.",
                parameters={
                    "message": ToolParameter(type="string", description="Texto con m!, ej m!p https://...", required=True),
                    "channel_id": ToolParameter(type="string", description="ID numerico del canal. Si no lo sabes con certeza, OMITILO (se usa el del .env). PROHIBIDO inventarlo"),
                },
            ),
            ToolSchema(
                name="music_control",
                description="PRIMERA OPCION para pausar/reanudar/saltar/frenar la musica de Discord. Solo toca Jockie, nunca YouTube/Spotify local. Rapido, sin buscar. Usar SIEMPRE en vez de m!pause/m!resume/m!skip/m!stop sueltos.",
                parameters={
                    "action": ToolParameter(type="string", description="pause, resume, skip o stop", required=True),
                },
            ),
        ]

    def get_intents(self) -> List[IntentPattern]:
        return [
            IntentPattern(
                name="discord_send",
                patterns=[
                    r"manda.*discord.*",
                    r"envi[aoe].*discord.*",
                    r"escrib[eí].*discord.*",
                    r"avis[aoe].*discord.*",
                    r"public[aoe].*discord.*",
                ],
                entities=["message"],
            ),
            IntentPattern(
                name="discord_command",
                patterns=[
                    r"pon.*musica.*",
                    r"reproduc.*",
                    r"paus.*musica.*",
                    r"reanud[aoe].*",
                    r"continu[aoe].*",
                    r"s[ií]gue.*",
                    r".*m!.*",
                    r"comand.*discord.*",
                    r"escrib.*discord.*",
                ],
                entities=["command"],
            ),
        ]
