import asyncio
from typing import Any, Dict, List

from plugins.base import IntentPattern, Permission, PluginBase, PluginManifest, ToolParameter, ToolSchema


class Plugin(PluginBase):
    manifest = PluginManifest(
        name="whatsapp",
        version="1.0.0",
        description="Enviar mensajes por WhatsApp Desktop",
        author="CLAUDIO",
        category="communication",
        dependencies=["pc_control"],
        permissions=[Permission.APP_LAUNCH],
    )

    async def initialize(self, core) -> None:
        self.core = core

    async def shutdown(self) -> None:
        pass

    async def tool_whatsapp_send(self, contact: str, message: str) -> Dict[str, Any]:
        pc = self.core.plugins.get("pc_control") if self.core is not None else None
        if pc is None:
            return {"error": "Se necesita el plugin pc_control instalado y cargado"}
        contact = (contact or "").strip()
        message = (message or "").strip()
        if not contact or not message:
            return {"error": "Indica contacto y mensaje"}
        r = await pc.execute_tool("focus_window", {"title_contains": "WhatsApp"})
        if not r.get("ok"):
            r = await pc.execute_tool("launch_app", {"app_name": "whatsapp"})
            if "error" in r:
                return {"error": f"No pude abrir WhatsApp: {r.get('error')}"}
            await asyncio.sleep(4.0)
            r = await pc.execute_tool("focus_window", {"title_contains": "WhatsApp"})
            if not r.get("ok"):
                return {"error": f"No pude enfocar WhatsApp: {r.get('error', r)}"}
        await pc.execute_tool("press_hotkey", {"keys": "ctrl+n"})
        await asyncio.sleep(1.0)
        await pc.execute_tool("type_text", {"text": contact})
        await asyncio.sleep(1.5)
        await pc.execute_tool("press_hotkey", {"keys": "enter"})
        await asyncio.sleep(1.5)
        await pc.execute_tool("type_text", {"text": message})
        await asyncio.sleep(0.5)
        r = await pc.execute_tool("press_hotkey", {"keys": "enter"})
        if not r.get("ok"):
            return {"error": f"No pude enviar: {r.get('error', r)}"}
        return {"ok": True, "contact": contact, "via": "whatsapp-desktop"}

    def get_tools(self) -> List[ToolSchema]:
        return [
            ToolSchema(
                name="whatsapp_send",
                description="Enviar un mensaje de WhatsApp a un contacto por la app Desktop (nuevo chat + escribir + enter). INTERRUMPE: toma foco y teclado.",
                parameters={
                    "contact": ToolParameter(type="string", description="Nombre del contacto o grupo", required=True),
                    "message": ToolParameter(type="string", description="Texto del mensaje", required=True),
                },
            ),
        ]

    def get_intents(self) -> List[IntentPattern]:
        return [
            IntentPattern(
                name="whatsapp_send",
                patterns=[
                    r"mand[aoe].*whatsapp.*",
                    r"escrib.*whatsapp.*",
                    r".*whatsapp.*",
                    r"decile.*por whatsapp.*",
                ],
                entities=["contact", "message"],
            ),
        ]
