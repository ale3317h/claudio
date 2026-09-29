import importlib.util
from pathlib import Path
from typing import Dict, List, Optional

import structlog
import yaml

from plugins.base import IntentPattern, CLAUDIOCore, PluginBase, PluginManifest, ToolSchema

logger = structlog.get_logger(__name__)


class PluginRegistry:
    def __init__(self, plugins_dir: Path, core: "CLAUDIOCore"):
        self.plugins_dir = plugins_dir
        self.core = core
        self.loaded: Dict[str, PluginBase] = {}
        self.manifests: Dict[str, PluginManifest] = {}

    def discover_plugins(self) -> List[Path]:
        plugin_paths = []
        for item in self.plugins_dir.iterdir():
            if item.is_dir() and not item.name.startswith("_"):
                manifest_path = item / "manifest.yaml"
                init_path = item / "__init__.py"
                if manifest_path.exists() and init_path.exists():
                    plugin_paths.append(item)
        return plugin_paths

    def load_manifest(self, plugin_path: Path) -> PluginManifest:
        manifest_path = plugin_path / "manifest.yaml"
        with open(manifest_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return PluginManifest(**data)

    def load_plugin_module(self, plugin_path: Path) -> PluginBase:
        module_name = f"plugins.{plugin_path.name}"
        spec = importlib.util.spec_from_file_location(
            module_name, plugin_path / "__init__.py"
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load plugin from {plugin_path}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        if not hasattr(module, "Plugin"):
            raise AttributeError(f"Plugin {plugin_path.name} must export 'Plugin' class")

        plugin_class = module.Plugin
        if not issubclass(plugin_class, PluginBase):
            raise TypeError("Plugin class must inherit from PluginBase")

        return plugin_class()

    def resolve_dependencies(self, manifests: Dict[str, PluginManifest]) -> List[str]:
        graph = {name: set(m.dependencies) for name, m in manifests.items()}
        visited = set()
        temp = set()
        order = []

        def visit(node: str):
            if node in temp:
                raise ValueError(f"Circular dependency detected: {node}")
            if node in visited:
                return
            temp.add(node)
            for dep in graph.get(node, []):
                visit(dep)
            temp.remove(node)
            visited.add(node)
            order.append(node)

        for node in graph:
            visit(node)

        return order

    async def load_all(self, enabled_only: bool = True) -> None:
        plugin_paths = self.discover_plugins()
        logger.info("plugins_discovered", count=len(plugin_paths))

        for plugin_path in plugin_paths:
            try:
                manifest = self.load_manifest(plugin_path)
                self.manifests[manifest.name] = manifest
                logger.debug("manifest_loaded", plugin=manifest.name)
            except Exception as e:
                logger.error("manifest_load_failed", plugin=plugin_path.name, error=str(e))

        if enabled_only:
            from core.config import settings
            enabled = set(settings.plugins.enabled)
            manifests_to_load = {
                k: v for k, v in self.manifests.items() if k in enabled
            }
        else:
            manifests_to_load = self.manifests

        load_order = self.resolve_dependencies(manifests_to_load)

        for name in load_order:
            plugin_path = self.plugins_dir / name
            try:
                plugin = self.load_plugin_module(plugin_path)
                await plugin.initialize(self.core)
                self.loaded[name] = plugin
                self.core.register_plugin(plugin)
                logger.info("plugin_loaded", plugin=name, version=plugin.manifest.version)
            except Exception as e:
                logger.error("plugin_load_failed", plugin=name, error=str(e))
                continue

    async def shutdown_all(self) -> None:
        for name in reversed(list(self.loaded.keys())):
            plugin = self.loaded[name]
            try:
                await plugin.shutdown()
                logger.info("plugin_shutdown", plugin=name)
            except Exception as e:
                logger.error("plugin_shutdown_failed", plugin=name, error=str(e))

    def get_plugin(self, name: str) -> Optional[PluginBase]:
        return self.loaded.get(name)

    def get_plugin_for_tool(self, tool_name: str) -> Optional[PluginBase]:
        owner = self.core.tool_owners.get(tool_name)
        if owner:
            return self.loaded.get(owner)
        return None

    def get_all_tools(self) -> Dict[str, "ToolSchema"]:
        return self.core.tools.copy()

    def get_all_intents(self) -> Dict[str, "IntentPattern"]:
        return self.core.intents.copy()

    def list_plugins(self) -> List[str]:
        return list(self.loaded.keys())


async def create_registry(plugins_dir: Path, core: "CLAUDIOCore") -> PluginRegistry:
    registry = PluginRegistry(plugins_dir, core)
    await registry.load_all()
    return registry