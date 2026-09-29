"""Probe de teclas push-to-talk: dice QUE tecla detecta tu PC.

Uso:
    .venv\\Scripts\\python scripts/probe_ptt.py

Mantené/apretá cada tecla cuando se pida (PAUSE, BLOQ NUM, SCROLL, CTRL DER...).
Muestra DOWN/UP por codigo: la que marque DOWN al apretar y UP al soltar sirve.
Ctrl+C para salir.
"""
import ctypes
import time

CANDIDATOS = [
    (0x13, "PAUSE"),
    (0x90, "BLOQ NUM"),
    (0x91, "SCROLL"),
    (0x2D, "INSERT"),
    (0xA3, "CTRL DER"),
    (0x5D, "MENU"),
]


def down(vk: int) -> bool:
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
    except Exception:
        return False


def main() -> None:
    print("Probe PTT: apreta y solta cada tecla varias veces.")
    print("Formato: <NOMBRE> DOWN/UP (codigo). Ctrl+C para salir.\n")
    estado = {vk: False for vk, _ in CANDIDATOS}
    t0 = time.time()
    try:
        while time.time() - t0 < 60:
            for vk, nombre in CANDIDATOS:
                ahora = down(vk)
                if ahora != estado[vk]:
                    estado[vk] = ahora
                    print(f"{nombre} {'DOWN' if ahora else 'UP'} ({hex(vk)})",
                          flush=True)
            time.sleep(0.02)
    except KeyboardInterrupt:
        pass
    print("\nListo. Pasame que teclas marcaron DOWN+UP.")


if __name__ == "__main__":
    main()
