import inspect
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class Permission(str, Enum):
    FILE_READ = "FILE_READ"
    FILE_WRITE = "FILE_WRITE"
    NETWORK = "NETWORK"
    SYSTEM = "SYSTEM"
    AUDIO = "AUDIO"
    SHELL = "SHELL"
    APP_LAUNCH = "APP_LAUNCH"


class ToolParameter(BaseModel):
    type: str = "string"
    description: str = ""
    enum: List[str] = []
    required: bool = False


class ToolSchema(BaseModel):
    name: str
    description: str
    parameters: Dict[str, ToolParameter] = Field(default_factory=dict)
    returns: str = "any"


class IntentPattern(BaseModel):
    name: str
    patterns: List[str]
    entities: List[str] = []
    priority: int = 0


class PluginManifest(BaseModel):
    name: str
    version: str
    description: str
    author: str = "CLAUDIO"
    category: str = "general"
    dependencies: List[str] = []
    permissions: List[Permission] = []
    entry_point: str = "plugin"


class PluginBase(ABC):
    manifest: PluginManifest

    def __init__(self):
        if not hasattr(self, "manifest"):
            raise NotImplementedError("Plugin must define 'manifest' class attribute")

    @abstractmethod
    async def initialize(self, core: "CLAUDIOCore") -> None:
        pass

    @abstractmethod
    async def shutdown(self) -> None:
        pass

    @abstractmethod
    def get_tools(self) -> List[ToolSchema]:
        pass

    @abstractmethod
    def get_intents(self) -> List[IntentPattern]:
        pass

    async def execute_tool(self, tool_name: str, args: Dict[str, Any]) -> Any:
        method = getattr(self, f"tool_{tool_name}", None)
        if method is None:
            raise NotImplementedError(
                f"Tool '{tool_name}' no implementada en plugin '{self.manifest.name}'"
            )
        call_args = dict(args or {})
        try:
            sig = inspect.signature(method)
            params = sig.parameters
            if not any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
                call_args = {k: v for k, v in call_args.items() if k in params}
        except (TypeError, ValueError):
            pass
        for v in call_args.values():
            if isinstance(v, str) and (("<" in v and ">" in v) or ("{{" in v and "}}" in v)):
                raise ValueError(
                    f"Argumento sin resolver '{v}': falta un dato real "
                    "(ej: un job_id). Pide el dato o consulta el estado antes."
                )
        result = method(**call_args)
        if inspect.isawaitable(result):
            result = await result
        return result


class CLAUDIOCore:
    def __init__(self):
        self.plugins: Dict[str, PluginBase] = {}
        self.tools: Dict[str, ToolSchema] = {}
        self.intents: Dict[str, IntentPattern] = {}
        self.tool_owners: Dict[str, str] = {}

    def register_plugin(self, plugin: PluginBase) -> None:
        self.plugins[plugin.manifest.name] = plugin
        for tool in plugin.get_tools():
            self.tools[tool.name] = tool
            self.tool_owners[tool.name] = plugin.manifest.name
        for intent in plugin.get_intents():
            self.intents[intent.name] = intent

    def get_tool(self, name: str) -> Optional[ToolSchema]:
        return self.tools.get(name)

    def get_intent(self, name: str) -> Optional[IntentPattern]:
        return self.intents.get(name)

    def list_plugins(self) -> List[str]:
        return list(self.plugins.keys())