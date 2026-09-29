import asyncio
import logging
import sys
from pathlib import Path

import structlog
import typer
from rich.console import Console
from rich.panel import Panel

if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.INFO))

import warnings

warnings.filterwarnings("ignore", message=".*automatic function calling.*")


class _DropAFCNoise(logging.Filter):
    """La libreria de Google loguea por stderr un aviso de AFC en cada llamada
    (no es error ni lo provocamos nosotros). Se descarta aca."""
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            return "automatic function calling" not in record.getMessage()
        except Exception:
            return True


logging.getLogger().addFilter(_DropAFCNoise())
for _noisy in ("google.genai.models", "google.genai", "langchain_google_genai"):
    logging.getLogger(_noisy).setLevel(logging.ERROR)
    logging.getLogger(_noisy).addFilter(_DropAFCNoise())

from core.brain import CLAUDIOBrain
from core.config import get_settings, settings
from core.events import event_bus
from core.memory import memory_system
from plugins.base import CLAUDIOCore
from plugins.registry import PluginRegistry, create_registry

app = typer.Typer(name="CLAUDIO", help="Asistente IA tipo Iron Man")
console = Console()

claudio_core = CLAUDIOCore()
plugin_registry: PluginRegistry = None
brain: CLAUDIOBrain = None


@event_bus.on("on_intent_classified")
async def log_intent(intent):
    console.print(f"[dim](intent) {intent.name} (conf: {intent.confidence:.2f})[/dim]")


@event_bus.on("on_tool_call")
async def log_tool_call(tool, args):
    console.print(f"[dim](tool) {tool} {args}[/dim]")


@event_bus.on("on_tool_result")
async def log_tool_result(tool, result):
    console.print(f"[dim](result) {tool}: {str(result)[:300]}[/dim]")


@event_bus.on("on_response_synthesized")
async def log_response(response):
    console.print(f"[green]CLAUDIO:[/green] {response}")


@event_bus.on("on_error")
async def log_error(event_name, *args, **kwargs):
    console.print(f"[red]Error in {event_name}: {args}[/red]")


def _load_banner() -> str:
    try:
        _banner = Path(__file__).resolve().parent.parent / "assets" / "banner.txt"
        if _banner.exists():
            return _banner.read_text(encoding="utf-8")
    except Exception:
        pass
    return ""


def _version() -> str:
    try:
        import subprocess
        r = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            capture_output=True, text=True, timeout=10,
            cwd=Path(__file__).resolve().parent.parent,
        )
        return (r.stdout or "").strip() or "dev"
    except Exception:
        return "dev"


def _rev() -> str:
    """Huella del código vivo: cambia con CUALQUIER edición (aunque no haya commit).

    Permite saber por captura si el proceso corre el código actual o uno viejo.
    """
    try:
        import hashlib
        h = hashlib.sha256()
        base = Path(__file__).resolve().parent
        for fn in ("brain.py", "llm.py", "main.py", "voice.py", "call.py",
                   "boot_anim.py", "config.py"):
            h.update((base / fn).read_bytes())
        pbase = base.parent / "plugins"
        for fn in ("music/__init__.py", "ytmusic/__init__.py",
                   "stt_whisper/__init__.py", "discord/__init__.py"):
            h.update((pbase / fn).read_bytes())
        return h.hexdigest()[:8]
    except Exception:
        return "?"


async def initialize(anim: bool = True):
    global plugin_registry, brain

    if anim:
        from core.boot_anim import play_boot_animation
        await play_boot_animation(console)
    _text = await asyncio.to_thread(_load_banner)
    if _text:
        console.print(_text, style="bold cyan", justify="center")
    console.print(Panel.fit(
        "[bold cyan]CLAUDIO[/bold cyan] - Inicializando...",
        border_style="cyan"
    ))

    console.print("[dim]Cargando configuración...[/dim]")
    get_settings()

    console.print("[dim]Iniciando memoria...[/dim]")
    memory_system.start_watching()
    # Arranque rapido: solo reindexa si la DB esta vacia. El watcher mantiene el resto.
    # Para forzar: python -m core.main reindex / reindex --full
    try:
        _count = memory_system.semantic_collection.count()
    except Exception:
        _count = 0
    if not _count:
        memory_system.reindex_vault(full=False)
    else:
        console.print(f"[dim]Indice existente ({_count} chunks), omito reindex.[/dim]")

    async def _bg_music_refresh():
        try:
            # Siempre: sync oficial (OAuth permanente, barato). Asi la lista
            # esta fresca en cada arranque sin que Señor haga nada.
            try:
                from core.yt_official import sync_official
                off = await asyncio.to_thread(sync_official, None, 30)
                if off.get("ok"):
                    import json as _json
                    from datetime import UTC, datetime

                    from core.mymusic_cache import apply_official_sync, cache_path, load_cache
                    out = apply_official_sync(load_cache(), off)
                    out["synced_at"] = datetime.now(UTC).isoformat(timespec="seconds")
                    out["synced_via"] = "boot-oficial-oauth"
                    cache_path().write_text(
                        _json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
                    console.print(f"[dim]Playlists al día: {len(out.get('playlists', []))} en memoria.[/dim]")
            except Exception as e:
                console.print(f"[dim]Sync oficial demorado ({str(e)[:80]}), sigo igual.[/dim]")
            from core.mymusic_cache import load_cache, refresh_if_stale
            if await refresh_if_stale():
                console.print("[dim]Gustos musicales actualizados.[/dim]")
            if not (load_cache().get("playlists") or []):
                console.print("[yellow]Sin playlists en cache. Corre "
                              "scripts/sync_youtube_official.py una vez.[/yellow]")
        except Exception:
            pass
    asyncio.create_task(_bg_music_refresh())

    console.print("[dim]Cargando plugins...[/dim]")
    plugins_dir = Path(__file__).parent.parent / "plugins"
    plugin_registry = await create_registry(plugins_dir, claudio_core)

    console.print("[dim]Construyendo cerebro...[/dim]")
    brain = CLAUDIOBrain(memory_system, plugin_registry)

    # Healthcheck a fondo: el arranque no espera (antes frenaba hasta 60s si
    # un backend colgaba). El hedge por latencia ya cubre el primer turno.
    async def _bg_healthcheck():
        try:
            results = await asyncio.wait_for(brain.llm_fast.healthcheck(), timeout=60)
        except Exception:
            results = []
        winner = next((n for n, ok, _, _ in results if ok), "")
        if results:
            parts = [f"{n} {'ok ' + str(ms) + 'ms' if ok else 'CAIDO ' + (e or '')}"
                     for n, ok, ms, e in results]
            console.print("[dim]Backends: " + " · ".join(parts) + "[/dim]")
        if winner:
            console.print(f"[bold cyan]Motor: {winner} (el primer sano)[/bold cyan]")
        else:
            console.print("[yellow]Sin backends en vivo (arranco igual, reintento al hablar).[/yellow]")
        for _bad, _err in (getattr(brain.llm_fast, "warmup_errors", []) or []):
            _err = _err or "sin respuesta"
            if _bad.startswith("groq") and "401" in _err:
                _hint = "key inválida: borrala o regenerala en console.groq.com"
            elif "429" in _err or "cuota" in _err:
                _hint = "cuota agotada: vuelve sola, rota a otra key"
            elif _bad == "ollama":
                _hint = "Ollama local no responde: abrí la app Ollama en tu PC"
            else:
                _hint = "reintento automático al hablar"
            console.print(f"[yellow]Backend caído: {_bad} ({_err}). {_hint}[/yellow]")
    asyncio.create_task(_bg_healthcheck())

    console.print(Panel.fit(
        f"[bold green]CLAUDIO listo {_version()} · rev {_rev()}[/bold green]",
        border_style="green"
    ))
    try:
        from core.store import Store
        _usage = Store(settings.memory.vault_path / "data").llm_usage_today()
        if _usage:
            _parts = [f"{k}: {v['calls']} llamadas (~{v['tokens']} tok)" for k, v in _usage.items()]
            console.print("[dim]Uso hoy: " + " · ".join(_parts) + "[/dim]")
    except Exception:
        pass


async def shutdown():
    console.print("[dim]Apagando CLAUDIO...[/dim]")
    memory_system.stop_watching()
    if plugin_registry:
        await plugin_registry.shutdown_all()
    console.print("[green]CLAUDIO apagado correctamente[/green]")


@app.command()
def run(
    voice: bool = typer.Option(False, "--voice", "-v", help="Modo voz (requiere STT/TTS)"),
    text: bool = typer.Option(False, "--text", "-t", help="Modo texto interactivo"),
    query: str = typer.Option("", "--query", "-q", help="Consulta única"),
    call: bool = typer.Option(False, "--call", help="Modo llamada manos libres"),
    anim: bool = typer.Option(True, "--anim/--no-anim", help="Animacion de arranque"),
    tray: bool = typer.Option(False, "--tray", help="Icono en bandeja del sistema"),
):
    """Ejecutar CLAUDIO"""
    asyncio.run(_run_async(voice, text, query, call, anim, tray))


async def _run_async(voice: bool, text: bool, query: str, call: bool, anim: bool = True,
                   tray: bool = False):
    await initialize(anim=anim)
    if tray:
        try:
            from core.tray import run_tray_background
            run_tray_background()
            console.print("[dim]Bandeja activada (icono J).[/dim]")
        except Exception as e:
            console.print(f"[yellow]Bandeja no disponible: {e}[/yellow]")

    try:
        if query:
            await brain.process(query)
        elif call:
            await _call_loop()
        elif text or not voice:
            await _interactive_text_loop()
        else:
            await _voice_loop()
    finally:
        await shutdown()


async def _interactive_text_loop():
    console.print("\n[dim]Modo texto interactivo. Escribe 'salir' para terminar.[/dim]\n")

    while True:
        try:
            user_input = console.input("[bold blue]Tú:[/bold blue] ").strip()
            if user_input.lower() in ("salir", "exit", "quit", "adios"):
                break
            if not user_input:
                continue

            await brain.process(user_input)
            console.print()

        except KeyboardInterrupt:
            break
        except EOFError:
            break


async def _call_loop():
    stt = plugin_registry.get_plugin("stt_whisper") if plugin_registry else None
    tts = plugin_registry.get_plugin("tts_edge") if plugin_registry else None
    if stt is None or tts is None:
        console.print("[yellow]Faltan plugins de voz (stt_whisper/tts_edge). Uso modo texto.[/yellow]")
        await _interactive_text_loop()
        return
    from core.call import CallLoop
    console.print("\n[dim]Modo llamada: habla cuando quieras. Deci 'salir' o Ctrl+C para cortar.[/dim]\n")
    await CallLoop(brain, stt, tts, sample_rate=settings.voice.sample_rate,
                   threshold=settings.voice.vad_threshold,
                   wake_enabled=settings.voice.wake_enabled,
                   wake_sensitivity=settings.voice.wake_sensitivity).run()


async def _voice_loop():
    stt = plugin_registry.get_plugin("stt_whisper") if plugin_registry else None
    tts = plugin_registry.get_plugin("tts_edge") if plugin_registry else None
    if stt is None or tts is None:
        console.print("[yellow]Faltan plugins de voz (stt_whisper/tts_edge). Uso modo texto.[/yellow]")
        await _interactive_text_loop()
        return
    from core.voice import VoiceLoop, _resolve_ptt_key
    _, _ptt_name = _resolve_ptt_key(settings.voice.ptt_key)
    console.print(f"\n[dim]Modo voz: mantené {_ptt_name} para hablar, soltá para enviar, ESC para salir.[/dim]\n")
    await VoiceLoop(
        brain, stt, tts,
        sample_rate=settings.voice.sample_rate,
        threshold=settings.voice.vad_threshold,
        silence_seconds=settings.voice.silence_seconds,
        max_record_seconds=settings.voice.max_record_seconds,
    ).run()


@app.command()
def reindex(full: bool = typer.Option(False, "--full", help="Reindexado completo")):
    """Re-indexar vault Obsidian"""
    asyncio.run(_reindex_async(full))


async def _reindex_async(full: bool):
    get_settings()
    memory_system.start_watching()
    memory_system.reindex_vault(full=full)
    memory_system.stop_watching()
    console.print("[green]Reindexado completado[/green]")


@app.command()
def test():
    """Ejecutar tests"""
    import subprocess
    result = subprocess.run(["pytest", "tests/", "-v"], cwd=Path(__file__).parent.parent)
    sys.exit(result.returncode)


@app.command()
def lint():
    """Ejecutar linters"""
    import subprocess
    subprocess.run(["ruff", "check", "."], cwd=Path(__file__).parent.parent)
    subprocess.run(["mypy", "core/", "plugins/"], cwd=Path(__file__).parent.parent)


@app.command()
def create_skill(
    name: str = typer.Argument(..., help="Nombre del skill"),
    category: str = typer.Option("general", "--category", "-c", help="Categoría"),
):
    """Crear template de skill/plugin"""
    asyncio.run(_create_skill_async(name, category))


async def _create_skill_async(name: str, category: str):
    plugins_dir = Path(__file__).parent.parent / "plugins"
    skill_dir = plugins_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)

    manifest = f"""name: "{name}"
version: "1.0.0"
description: "Skill {name} para CLAUDIO"
author: "CLAUDIO"
category: "{category}"
dependencies: []
permissions: []
entry_point: "plugin"
"""
    (skill_dir / "manifest.yaml").write_text(manifest, encoding="utf-8")

    init_content = f'''from plugins.base import PluginBase, PluginManifest, Permission, ToolSchema, ToolParameter, IntentPattern

class Plugin(PluginBase):
    manifest = PluginManifest(
        name="{name}",
        version="1.0.0",
        description="Skill {name} para CLAUDIO",
        author="CLAUDIO",
        category="{category}",
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
                name="{name}_example",
                description="Ejemplo de herramienta",
                parameters={{}},
            )
        ]

    def get_intents(self) -> list[IntentPattern]:
        return [
            IntentPattern(
                name="{name}_example",
                patterns=[f"ejemplo {name}", f"test {name}"],
                entities=[],
            )
        ]
'''
    (skill_dir / "__init__.py").write_text(init_content, encoding="utf-8")

    console.print(f"[green]Skill '{name}' creado en {skill_dir}[/green]")


if __name__ == "__main__":
    app()