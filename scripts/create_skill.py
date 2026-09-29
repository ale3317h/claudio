#!/usr/bin/env python
"""Script para crear template de skill/plugin"""
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config import get_settings


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Crear template de skill/plugin")
    parser.add_argument("name", help="Nombre del skill")
    parser.add_argument("--category", "-c", default="general", help="Categoría")
    args = parser.parse_args()

    get_settings()

    plugins_dir = Path(__file__).parent.parent / "plugins"
    skill_dir = plugins_dir / args.name
    skill_dir.mkdir(parents=True, exist_ok=True)

    manifest = f'''name: "{args.name}"
version: "1.0.0"
description: "Skill {args.name} para CLAUDIO"
author: "CLAUDIO"
category: "{args.category}"
dependencies: []
permissions: []
entry_point: "plugin"
'''
    (skill_dir / "manifest.yaml").write_text(manifest, encoding="utf-8")

    init_content = f'''from plugins.base import PluginBase, PluginManifest, Permission, ToolSchema, ToolParameter, IntentPattern

class Plugin(PluginBase):
    manifest = PluginManifest(
        name="{args.name}",
        version="1.0.0",
        description="Skill {args.name} para CLAUDIO",
        author="CLAUDIO",
        category="{args.category}",
        dependencies=[],
        permissions=[],
    )

    async def initialize(self, core):
        pass

    async def shutdown(self):
        pass

    def get_tools(self) -> list[ToolSchema]:
        return [
            ToolSchema(
                name="{args.name}_example",
                description="Ejemplo de herramienta",
                parameters={{}},
            )
        ]

    def get_intents(self) -> list[IntentPattern]:
        return [
            IntentPattern(
                name="{args.name}_example",
                patterns=["ejemplo {args.name}", "test {args.name}"],
                entities=[],
            )
        ]
'''
    (skill_dir / "__init__.py").write_text(init_content, encoding="utf-8")

    print(f"[OK] Skill '{args.name}' creado en {skill_dir}")
    print(f"   Edita {skill_dir}/__init__.py para implementar la lógica")


if __name__ == "__main__":
    main()