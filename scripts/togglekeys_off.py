"""Apaga el bip de Bloq Num/Caps/Scroll (ToggleKeys) ya mismo y para siempre.

Uso (en TU terminal, una sola vez):
    .venv\\Scripts\\python scripts\\togglekeys_off.py
"""
import ctypes
import winreg

SPI_GET = 0x0036
SPI_SET = 0x0037
SPIF = 0x03  # guarda + avisa al sistema


class TOGGLEKEYS(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwFlags", ctypes.c_uint)]


def main() -> None:
    u = ctypes.windll.user32
    cur = TOGGLEKEYS(8, 0)
    ok_get = u.SystemParametersInfoW(SPI_GET, 0, ctypes.byref(cur), 0)
    print(f"estado actual: ok={bool(ok_get)} flags={cur.dwFlags} (1=prendido)")
    new = TOGGLEKEYS(8, 0)
    ok_set = u.SystemParametersInfoW(SPI_SET, 0, ctypes.byref(new), SPIF)
    print(f"apagado en vivo: {bool(ok_set)} (err={ctypes.get_last_error()})")
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                        r"Control Panel\Accessibility\ToggleKeys",
                        0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "Flags", 0, winreg.REG_SZ, "62")
    print("registro: Flags=62 (queda apagado para siempre)")


if __name__ == "__main__":
    main()
