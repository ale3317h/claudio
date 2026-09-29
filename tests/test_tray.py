def test_tray_icon_se_genera():
    from core.tray import _make_icon
    img = _make_icon(64)
    assert img.size == (64, 64) and img.mode == "RGBA"


def test_tray_menu_items():
    from core.tray import _default_status, _menu_items, start_tray
    items = _menu_items(lambda: "CLAUDIO · test")
    labels = [str(it.text) for it in items]
    assert labels[0] == "CLAUDIO · test"
    assert "Abrir vault" in labels and "Salir" in labels
    assert isinstance(_default_status(), str) and _default_status()
    icon = start_tray(lambda: "CLAUDIO · test")
    assert icon.name == "CLAUDIO"
