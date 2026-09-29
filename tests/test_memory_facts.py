from core.memory import memory_system


def test_append_memory_fact(tmp_path):
    f = tmp_path / "memoria.md"
    assert memory_system.append_memory_fact("A Señor le gusta el rock nacional", path=f) is True
    assert memory_system.append_memory_fact("A Señor le gusta el rock nacional", path=f) is False
    assert memory_system.append_memory_fact("corto", path=f) is False
    assert "rock nacional" in f.read_text(encoding="utf-8")
