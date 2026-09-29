from typing import Any, Dict, List

from plugins.base import IntentPattern, PluginBase, PluginManifest, ToolParameter, ToolSchema


def _coerce(value: str) -> Any:
    text = (value or "").strip()
    if not text:
        return text
    import json
    try:
        return json.loads(text)
    except Exception:
        return text


class Plugin(PluginBase):
    manifest = PluginManifest(
        name="memory",
        version="1.0.0",
        description="Memoria estructurada: datos, eventos y preferencias",
        author="CLAUDIO",
        category="memory",
        dependencies=[],
        permissions=[],
    )

    async def initialize(self, core) -> None:
        from core.config import settings
        from core.store import Store
        self.store = Store(settings.memory.vault_path / "data")
        self.core = core

    async def shutdown(self) -> None:
        pass

    async def tool_remember(self, key: str, value: str) -> Dict[str, Any]:
        if self.store.remember(key, value, source="chat"):
            return {"ok": True, "key": key.strip().lower()}
        return {"error": "Indica clave y valor no vacios"}

    async def tool_recall(self, key: str) -> Dict[str, Any]:
        value = self.store.recall(key)
        if value is None:
            return {"error": f"No recuerdo nada sobre '{key}'"}
        return {"ok": True, "key": key.strip().lower(), "value": value}

    async def tool_forget(self, key: str) -> Dict[str, Any]:
        if self.store.forget(key):
            return {"ok": True, "key": key.strip().lower()}
        return {"error": f"No habia nada sobre '{key}'"}

    async def tool_get_pref(self, key: str) -> Dict[str, Any]:
        return {"ok": True, "key": key, "value": self.store.get_pref(key)}

    async def tool_set_pref(self, key: str, value: str) -> Dict[str, Any]:
        self.store.set_pref(key, _coerce(value))
        return {"ok": True, "key": key, "value": self.store.get_pref(key)}

    def get_tools(self) -> List[ToolSchema]:
        return [
            ToolSchema(
                name="remember",
                description="Guardar un dato con clave para recordarlo despues (ej: color_favorito -> azul).",
                parameters={
                    "key": ToolParameter(type="string", description="Clave corta en minusculas con guiones bajos", required=True),
                    "value": ToolParameter(type="string", description="Dato a guardar", required=True),
                },
            ),
            ToolSchema(
                name="recall",
                description="Recuperar un dato guardado por su clave.",
                parameters={
                    "key": ToolParameter(type="string", description="Clave a buscar", required=True),
                },
            ),
            ToolSchema(
                name="forget",
                description="Olvidar un dato guardado.",
                parameters={
                    "key": ToolParameter(type="string", description="Clave a borrar", required=True),
                },
            ),
            ToolSchema(
                name="get_pref",
                description="Leer una preferencia de configuracion.",
                parameters={
                    "key": ToolParameter(type="string", description="Nombre de la preferencia", required=True),
                },
            ),
            ToolSchema(
                name="set_pref",
                description="Cambiar una preferencia de configuracion.",
                parameters={
                    "key": ToolParameter(type="string", description="Nombre de la preferencia", required=True),
                    "value": ToolParameter(type="string", description="Nuevo valor", required=True),
                },
            ),
        ]

    def get_intents(self) -> List[IntentPattern]:
        return [
            IntentPattern(
                name="memory_store",
                patterns=[
                    r"record[aá].*",
                    r"acordate.*",
                    r"guardate.*",
                    r"no te olvides.*",
                    r"memoriza.*",
                ],
                entities=["key", "value"],
            ),
            IntentPattern(
                name="memory_recall",
                patterns=[
                    r"qu[eé] sabes de.*",
                    r"te acordas.*",
                    r"recordame.*",
                    r"cu[aá]l es mi.*",
                ],
                entities=["key"],
            ),
            IntentPattern(
                name="memory_pref",
                patterns=[
                    r"preferencia.*",
                    r"configura.*",
                    r"cambia.*preferencia.*",
                ],
                entities=["key", "value"],
            ),
        ]
