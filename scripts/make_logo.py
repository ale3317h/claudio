"""Genera el logo CLAUDIO estilo arc-reactor (fondo transparente)."""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

SIZE = 1024
C = SIZE // 2
CYAN = (0, 229, 255, 255)
WHITE = (255, 255, 255, 255)

layer = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
d = ImageDraw.Draw(layer)


def ring(r, width, color=CYAN):
    d.ellipse([C - r, C - r, C + r, C + r], outline=color, width=width)


def arc_seg(r, start, end, width, color=CYAN):
    d.arc([C - r, C - r, C + r, C + r], start=start, end=end, fill=color, width=width)


def glow_dot(r, color):
    d.ellipse([C - r, C - r, C + r, C + r], fill=color)


# Nucleo con resplandor
for r, a in [(150, 40), (120, 70), (95, 110), (70, 160)]:
    glow_dot(r, (0, 229, 255, a))
glow_dot(48, WHITE)
glow_dot(38, CYAN)

# Anillo interno
ring(185, 14)

# Anillo segmentado (24 gajos con 3 huecos)
for i in range(24):
    if i % 8 == 4:
        continue
    a0 = i * 15
    arc_seg(248, a0 + 2, a0 + 11, 18)

# Ticks de precision (60 marcas)
for i in range(60):
    a = math.radians(i * 6)
    long = i % 5 == 0
    r0, r1 = (300, 322) if long else (306, 318)
    w = 6 if long else 3
    d.line(
        [C + r0 * math.cos(a), C + r0 * math.sin(a),
         C + r1 * math.cos(a), C + r1 * math.sin(a)],
        fill=CYAN, width=w,
    )

# Anillo externo grueso
ring(368, 24)

# Arcos orbitales externos
for start in (200, 320):
    arc_seg(428, start, start + 70, 12)
    arc_seg(428, start + 180, start + 250, 12)

# Halo: copia difuminada debajo del dibujo nitido
halo = layer.filter(ImageFilter.GaussianBlur(16))
logo = Image.alpha_composite(halo, layer)

out_dir = Path(__file__).resolve().parent.parent / "assets"
out_dir.mkdir(exist_ok=True)
logo.save(out_dir / "claudio_logo.png")
logo.save(Path(__file__).resolve().parent.parent / "logo.ico",
           sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("LOGO_OK")
