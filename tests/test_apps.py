from plugins.browser import Plugin as BrowserPlugin
from plugins.pc_control import _first_existing, _launch_store_app
from plugins.whatsapp import Plugin as WhatsappPlugin


def test_browser_schema():
    p = BrowserPlugin()
    names = {t.name for t in p.get_tools()}
    assert {"browser_open", "browser_tabs", "browser_read"} <= names
    assert any(i.name == "browser_open" for i in p.get_intents())


def test_whatsapp_schema():
    p = WhatsappPlugin()
    assert any(t.name == "whatsapp_send" for t in p.get_tools())
    assert any(i.name == "whatsapp_send" for i in p.get_intents())


def test_first_existing():
    assert _first_existing(["C:\\no\\existe\\x.exe", ""]) == ""


def test_store_app_no_match():
    assert _launch_store_app("zzzz_no_existe_123") is None
