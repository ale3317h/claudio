from core.store import Store


def _store(tmp_path):
    return Store(tmp_path)


def test_facts_crud(tmp_path):
    s = _store(tmp_path)
    assert s.recall("color") is None
    assert s.remember("color_favorito", "azul") is True
    assert s.recall("color_favorito") == "azul"
    assert s.remember("color_favorito", "rojo") is True
    assert s.recall("color_favorito") == "rojo"
    assert s.remember("", "x") is False
    assert s.forget("color_favorito") is True
    assert s.forget("color_favorito") is False
    assert s.recall("COLOR_FAVORITO") is None


def test_facts_list(tmp_path):
    s = _store(tmp_path)
    s.remember("musica_rock", "si")
    s.remember("musica_pop", "no")
    s.remember("comida", "asado")
    assert set(s.list_facts("musica_")) == {"musica_rock", "musica_pop"}
    assert len(s.list_facts()) == 3


def test_events(tmp_path):
    s = _store(tmp_path)
    assert s.recent_events() == []
    s.log_event("musica", "reprodujo tranqui")
    s.log_event("chat", "hola")
    rec = s.recent_events(limit=10)
    assert [e["kind"] for e in rec] == ["chat", "musica"]
    assert s.recent_events(kind="musica")[0]["content"] == "reprodujo tranqui"


def test_prefs(tmp_path):
    s = _store(tmp_path)
    assert s.get_pref("language") == "es"
    assert s.get_pref("inexistente", "d") == "d"
    s.set_pref("volume", 7)
    s.set_pref("modo", "noche")
    again = Store(tmp_path)
    assert again.get_pref("volume") == 7
    assert again.get_pref("modo") == "noche"
    assert again.all_prefs()["language"] == "es"


def test_memory_plugin_schema():
    from plugins.memory import Plugin
    p = Plugin()
    names = {t.name for t in p.get_tools()}
    assert {"remember", "recall", "forget", "get_pref", "set_pref"} <= names
    assert any(i.name == "memory_store" for i in p.get_intents())
