#!/usr/bin/env python
"""Script para re-indexar el vault de Obsidian"""
import asyncio
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config import get_settings
from core.memory import memory_system


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Re-indexar vault Obsidian")
    parser.add_argument("--full", action="store_true", help="Reindexado completo (borra BD anterior)")
    args = parser.parse_args()

    get_settings()

    print(f"Vault: {memory_system.vault_path}")
    print(f"Full reindex: {args.full}")

    memory_system.start_watching()
    try:
        memory_system.reindex_vault(full=args.full)
    finally:
        memory_system.stop_watching()

    print("[OK] Reindexado completado")


if __name__ == "__main__":
    asyncio.run(main())