"""Chequeo de precision del wake-word Vosk (positivo + control)."""
import asyncio
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.wake import VoskSpotter
from plugins.tts_edge import Plugin as Tts


def to_16k(path: str) -> np.ndarray:
    data, sr = sf.read(path, dtype="float32", always_2d=False)
    x = np.asarray(data, dtype=np.float64).flatten()
    if sr != 16000:
        x = np.interp(np.linspace(0, len(x), int(len(x) * 16000 / sr)), np.arange(len(x)), x)
    return x.astype(np.float32)


async def tts_file(text: str) -> str:
    t = Tts()
    await t.initialize(None)
    r = await t.execute_tool("speak", {"text": text, "play": False})
    assert r.get("file"), r
    return r["file"]


async def main() -> None:
    f1 = await tts_file("Hey claudio, poné música.")
    f2 = await tts_file("Hola, qué lindo día para pasear por el parque.")
    spotter = VoskSpotter(cooldown_seconds=0.0)
    spotter.start()
    ok = True
    for label, f, expected in (("POSITIVO", f1, True), ("CONTROL", f2, False)):
        audio = to_16k(f)
        fired = False
        for i in range(0, len(audio), 4000):
            if spotter.feed(audio[i:i + 4000]):
                fired = True
                break
        print(label, "->", fired, "(esperado:", expected, ")")
        ok = ok and (fired == expected)
    spotter.stop()
    print("WAKE_CHECK:", "OK" if ok else "FALLO")
    if not ok:
        sys.exit(1)


asyncio.run(main())
