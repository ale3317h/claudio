import asyncio

from core.brain import _ahora_str
from plugins.system import Plugin as SystemPlugin


def test_system_schema():
    p = SystemPlugin()
    names = {t.name for t in p.get_tools()}
    assert {"system_time", "system_stats"} <= names
    assert any(i.name == "system_time" for i in p.get_intents())


def test_system_time_live():
    p = SystemPlugin()
    res = asyncio.run(p.execute_tool("system_time", {}))
    assert res.get("ok") is True
    assert ":" in res.get("hora", "")
    assert "de" in res.get("fecha", "")


def test_system_stats_live():
    p = SystemPlugin()
    res = asyncio.run(p.execute_tool("system_stats", {}))
    assert res.get("ok") is True
    assert 0 <= res["cpu_pct"] <= 100
    assert 0 <= res["ram_pct"] <= 100
    assert res["ram_total_gb"] > 0


def test_ahora_str():
    import re
    text = _ahora_str()
    assert re.match(r"^\w+ \d+ de \w+ de \d{4}, \d{2}:\d{2}$", text), text
    assert "2026" in text


def test_version_string():
    from core.main import _version
    assert isinstance(_version(), str) and len(_version()) > 0
