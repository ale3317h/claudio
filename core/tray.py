"""Icono de bandeja (Fase 6 GUI): estado + accesos + salir.

Uso: python -m core.main run --voice --tray
Corre en thread daemon; el loop principal sigue en el hilo principal.
"""
import os
import threading


def _make_icon(size: int = 64):
    """Icono CLAUDIO generado (sin assets): fondo oscuro + J cyan."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (10, 14, 20, 255))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([2, 2, size - 3, size - 3], radius=size // 5,
                        fill=(13, 20, 32, 255), outline=(0, 229, 255, 255),
                        width=max(2, size // 32))
    d.text((size // 2, size // 2 - 1), "J", fill=(0, 229, 255, 255),
           anchor="mm", font=None)
    return img


def _default_status() -> str:
    try:
        from core.config import settings
        from core.store import Store
        usage = Store(settings.memory.vault_path / "data").llm_usage_today()
        parts = [f"{k}: {v['calls']}" for k, v in usage.items()]
        return "CLAUDIO · hoy " + (", ".join(parts) if parts else "sin llamadas")
    except Exception:
        return "CLAUDIO"


def _open_path(path: str) -> None:
    try:
        os.startfile(os.path.abspath(path))  # noqa: S606 (ruta local propia)
    except Exception:
        pass


def _menu_items(status_fn=None):
    from pystray import MenuItem as Item
    status_fn = status_fn or _default_status

    def _vault(icon, item):
        try:
            from core.config import settings
            _open_path(str(settings.memory.vault_path))
        except Exception:
            pass

    def _memoria(icon, item):
        try:
            from core.config import settings
            _open_path(str(settings.memory.vault_path / "memoria.md"))
        except Exception:
            pass

    def _quit(icon, item):
        try:
            icon.stop()
        finally:
            os._exit(0)

    return (
        Item(status_fn(), lambda i, it: None, enabled=False),
        Item("Abrir vault", _vault),
        Item("Abrir memoria", _memoria),
        Item("Salir", _quit),
    )


def start_tray(status_fn=None):
    """Crea el icono (sin correrlo). Para tests y para main."""
    import pystray
    return pystray.Icon("CLAUDIO", _make_icon(), "CLAUDIO",
                        _menu_items(status_fn))


def run_tray_background(status_fn=None):
    """Corre el icono en thread daemon. Devuelve el thread."""
    icon = start_tray(status_fn)
    th = threading.Thread(target=icon.run, daemon=True, name="claudio-tray")
    th.start()
    return th
