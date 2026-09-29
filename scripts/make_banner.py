"""Genera assets/banner.txt (arte ASCII del reactor) desde el logo PNG."""
from pathlib import Path

from PIL import Image

RAMP = " .:-=+*#%@"
W = 56


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    img = Image.open(root / "assets" / "claudio_logo.png").convert("L")
    h = max(1, int(img.height * W / img.width * 0.5))
    img = img.resize((W, h))
    lines = []
    for y in range(h):
        row = "".join(RAMP[img.getpixel((x, y)) * (len(RAMP) - 1) // 255] for x in range(W))
        lines.append(row.rstrip() or " ")
    lines += ["", "J A R V I S"]
    (root / "assets" / "banner.txt").write_text("\n".join(lines), encoding="utf-8")
    print(f"BANNER_OK {len(lines)} lineas")


if __name__ == "__main__":
    main()
