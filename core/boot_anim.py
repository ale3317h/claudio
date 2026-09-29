"""Animacion de arranque: Arc Reactor ASCII (segun imagen de referencia).

Anillos segmentados contrarrotantes, pulsos de energia viajando por los
anillos, nucleo denso parpadeante con bobina horizontal y acentos amarillos.
Puro ASCII (seguro en cualquier codepage) + color via rich.
Pura (sin I/O) + player con rich. Se salta sola si no hay TTY.
"""
import math
import random

W, H = 68, 26
CX, CY = W // 2, 12
# Las celdas de terminal son ~2x mas altas que anchas: se compensa en X.
ASPECT = 0.5

# radio, grosor, velocidad (giro/frame), segmentos, patron, duty
RINGS = [
    (10.6, 0.42, 0.045, 56, "-+", 0.85),
    (9.2, 0.32, -0.07, 44, ":.", 0.70),
    (7.8, 0.42, 0.10, 28, "+-", 0.80),
    (6.4, 0.32, -0.13, 22, "++--", 0.75),
    (5.0, 0.38, 0.17, 16, "+-", 0.85),
]
CORE_R = 3.4
CORE_CHARS = "#%*+*=+"


def _polar(x, y):
    dx = (x - CX) * ASPECT
    dy = y - CY
    return math.hypot(dx, dy), math.atan2(dy, dx)


def _ring_cell(r, th, frame):
    for idx, (rr, w, sp, segs, pat, duty) in enumerate(RINGS):
        if abs(r - rr) <= w:
            p = ((th / (2.0 * math.pi) + frame * sp) % 1.0) * segs
            frac = p - math.floor(p)
            ch = pat[int(p) % len(pat)] if frac < duty else "."
            return ch, idx
    return None


def _is_yellow_accent(ring_idx, th) -> bool:
    # Acentos amarillos como en la referencia: arco superior-derecho e inferior.
    return ring_idx <= 1 and ((-1.30 < th < -0.15) or (0.90 < th < 2.20))


def _style_for(energy: float, yellow: bool) -> str:
    if yellow:
        return "bold yellow" if energy > 0.55 else "dim yellow"
    if energy > 0.82:
        return "bold bright_white"
    if energy > 0.60:
        return "bold bright_cyan"
    if energy > 0.35:
        return "cyan"
    return "dim cyan"


def render_frame(frame: int, seed: int = 7):
    """Devuelve rich Text con el reactor en el instante `frame`."""
    from rich.text import Text
    text = Text()
    for y in range(H):
        for x in range(W):
            r, th = _polar(x, y)
            ch, st = " ", ""
            if r < CORE_R:
                frng = random.Random(seed * 100003 + x * 73 + y * 57 + frame // 2)  # noqa: S311
                flick = frng.random()
                if abs(y - CY) <= 0.9 and r < 5.0 and (x + frame) % 2 == 0:
                    ch = "="  # bobina horizontal del nucleo
                    st = "bold bright_white" if flick > 0.4 else "bold cyan"
                elif flick > 0.25:
                    ch = CORE_CHARS[int(flick * len(CORE_CHARS)) % len(CORE_CHARS)]
                    st = "bold bright_white" if flick > 0.72 else "bold bright_cyan"
            else:
                hit = _ring_cell(r, th, frame)
                if hit is not None:
                    ch, idx = hit
                    energy = 0.5 + 0.5 * math.sin(frame * 0.35 - r * 1.1 + idx * 0.7)
                    st = _style_for(energy, _is_yellow_accent(idx, th))
                else:
                    srng = random.Random(seed * 7919 + x * 31 + y * 17)  # noqa: S311
                    if srng.random() > 0.985:
                        ch, st = ".", "dim cyan"  # polvo estatico exterior
            text.append(ch, style=st or "")
        text.append("\n")
    title = "J A R V I S"
    pad = " " * ((W - len(title)) // 2)
    text.append(pad)
    for c in title:
        text.append(c, style="bold cyan")
    text.append("\n")
    return text


async def play_boot_animation(console, seconds: float = 2.0, fps: int = 12) -> None:
    """Muestra la animacion salvo que no haya terminal interactiva."""
    try:
        if not console.is_terminal:
            return
    except Exception:
        return
    import asyncio

    from rich.live import Live
    frames = max(1, int(seconds * fps))
    try:
        with Live(console=console, refresh_per_second=fps, transient=True) as live:
            for f in range(frames):
                live.update(render_frame(f))
                await asyncio.sleep(1.0 / fps)
    except Exception:
        pass


def fire_once() -> str:
    """Linea estatica para contextos sin animacion."""
    return "-=+ J A R V I S +=-"


def _self_test() -> None:
    a = render_frame(0).plain
    b = render_frame(0).plain
    assert a == b and "J A R V I S" in a  # noqa: S101
    assert render_frame(0).plain != render_frame(6).plain  # noqa: S101
    print("boot_anim ok")


if __name__ == "__main__":
    _self_test()
