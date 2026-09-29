import asyncio
import os

import pytest

from plugins.discord import Plugin as DiscordPlugin
from plugins.pc_control import Plugin as PcPlugin


def test_pc_tools_registered():
    p = PcPlugin()
    names = {t.name for t in p.get_tools()}
    assert {"launch_app", "list_windows", "focus_window", "close_window", "type_text", "press_hotkey", "run_shell"} <= names
    assert any(i.name == "pc_launch" for i in p.get_intents())


def test_list_windows_safe():
    p = PcPlugin()
    res = asyncio.run(p.execute_tool("list_windows", {}))
    assert isinstance(res, list)
    assert all("title" in w for w in res)


def test_shell_disabled_by_default():
    p = PcPlugin()
    p.allow_shell = False
    res = asyncio.run(p.execute_tool("run_shell", {"command": "echo hola"}))
    assert "error" in res


def test_discord_tools_registered():
    p = DiscordPlugin()
    assert any(t.name == "discord_send" for t in p.get_tools())
    assert any(i.name == "discord_send" for i in p.get_intents())


def test_discord_no_webhook():
    os.environ.pop("DISCORD_WEBHOOK", None)
    p = DiscordPlugin()
    res = asyncio.run(p.execute_tool("discord_send", {"message": "hola"}))
    assert "error" in res


def test_discord_send_rechaza_correcciones():
    from plugins.discord import Plugin as DiscordPlugin
    p = DiscordPlugin()
    res = asyncio.run(p.execute_tool(
        "discord_send", {"message": "Mentira, no lo hiciste. Ya tiene que pensar que es Discord."}))
    assert "error" in res


def test_execute_tool_ignores_unknown_kwargs():
    p = PcPlugin()
    res = asyncio.run(p.execute_tool("list_windows", {"inventado": 123}))
    assert isinstance(res, list)


def test_discord_accepts_content_alias():
    os.environ.pop("DISCORD_WEBHOOK", None)
    p = DiscordPlugin()
    res = asyncio.run(p.execute_tool("discord_send", {"content": "hola"}))
    assert "error" in res
    assert "inesperado" not in res["error"] and "unexpected" not in res["error"]


def test_input_struct_size():
    import ctypes

    from plugins.pc_control import Input
    assert ctypes.sizeof(Input) == 40


def test_discord_alias_resolves():
    import os

    from plugins.pc_control import _app_aliases
    exe, alias_args = _app_aliases()["discord"]
    assert os.path.exists(exe)
    assert "Discord.exe" in alias_args


def test_discord_user_send_needs_token():
    import os
    os.environ.pop("DISCORD_USER_TOKEN", None)
    from plugins.discord import Plugin as DiscordPlugin
    p = DiscordPlugin()
    res = asyncio.run(p.execute_tool("discord_user_send", {"message": "hola"}))
    assert "error" in res


def test_job_start_blocked_without_shell():
    p = PcPlugin()
    p.allow_shell = False
    res = asyncio.run(p.execute_tool("job_start", {"command": "echo hola"}))
    assert "error" in res


def test_job_roundtrip():
    import time
    p = PcPlugin()
    p.allow_shell = True
    started = asyncio.run(p.execute_tool("job_start", {"command": "echo hola", "name": "t"}))
    assert started.get("ok") is True
    job_id = started["job_id"]
    result = {"status": "queued"}
    for _ in range(50):
        result = asyncio.run(p.execute_tool("job_result", {"job_id": job_id}))
        if result.get("ok") is True or result.get("status") == "error":
            break
        time.sleep(0.2)
    assert result.get("ok") is True
    assert "hola" in result.get("stdout", "")


def test_silent_kwargs_no_window():
    import sys

    from plugins.pc_control import _silent_popen_kwargs
    kw = _silent_popen_kwargs()
    if sys.platform == "win32":
        import subprocess
        assert kw.get("creationflags", 0) & subprocess.CREATE_NO_WINDOW
        assert "startupinfo" in kw


def test_placeholder_args_rejected():
    import os
    os.environ["DISCORD_WEBHOOK"] = "https://example.com/hook"
    try:
        from plugins.discord import Plugin as DiscordPlugin
        p = DiscordPlugin()
        with pytest.raises(ValueError, match="sin resolver"):
            asyncio.run(p.execute_tool("discord_send", {"message": "<ID_obtenido_de_job_status>"}))
    finally:
        os.environ.pop("DISCORD_WEBHOOK", None)


def test_jobs_persist(tmp_path):
    import time

    from plugins import pc_control as pc_mod
    p = PcPlugin()
    p.allow_shell = True
    f = tmp_path / "jobs.json"
    pc_mod._restore_jobs(f)
    started = asyncio.run(p.execute_tool("job_start", {"command": "echo hola", "name": "t"}))
    assert started.get("ok") is True
    jid = started["job_id"]
    for _ in range(50):
        with pc_mod._JOBS_LOCK:
            st = pc_mod._JOBS.get(jid, {}).get("status")
        if st in ("done", "error"):
            break
        time.sleep(0.2)
    pc_mod._JOBS.clear()
    pc_mod._restore_jobs(f)
    assert jid in pc_mod._JOBS, f"jobs file: {f.read_text() if f.exists() else 'MISSING'}"
    assert pc_mod._JOBS[jid]["status"] == "done"


def test_discord_user_send_ignores_fake_channel_id(monkeypatch):
    import asyncio

    import httpx

    from plugins.discord import Plugin
    monkeypatch.setenv("DISCORD_USER_TOKEN", "tok123")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "123456789012345678")
    seen = {}
    class FakeResp:
        status_code = 200
        text = ""
    class FakeClient:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, url, headers=None, json=None):
            seen["url"] = url
            return FakeResp()
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    p = Plugin()
    res = asyncio.run(p.execute_tool("discord_user_send", {"message": "m!pause", "channel_id": "ID_DEL_CANAL_DE_DISCORD"}))
    assert res.get("ok") is True
    assert "123456789012345678" in seen["url"]


def test_music_control_uses_discord_when_recent(monkeypatch):
    import asyncio

    import httpx

    from plugins.discord import Plugin
    monkeypatch.setenv("DISCORD_USER_TOKEN", "tok123")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "123456789012345678")
    seen = {}
    class FakeResp:
        status_code = 200
        text = ""
    class FakeClient:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, url, headers=None, json=None):
            seen["json"] = json
            return FakeResp()
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    assert not hasattr(Plugin, "_media_key"), "music_control no debe tocar teclas del sistema"
    p = Plugin()
    res = asyncio.run(p.execute_tool("music_control", {"action": "pause"}))
    assert res.get("ok") is True and res["command"] == "m!pause" and res["source"] == "discord"
    assert seen["json"]["content"] == "m!pause"


def test_music_control_bad_action():
    import asyncio

    from plugins.discord import Plugin
    res = asyncio.run(Plugin().execute_tool("music_control", {"action": "bailar"}))
    assert "error" in res


def test_fase1_close_open_intents():
    import re

    from plugins.pc_control import Plugin
    pats = {i.name: i.patterns for i in Plugin().get_intents()}
    assert any(re.search(p, "che abrime el discord") for p in pats["pc_launch"])
    assert any(re.search(p, "cerralo") for p in pats["pc_close"])
    assert any(re.search(p, "cerra esa ventana") for p in pats["pc_close"])
    assert any(re.search(p, "no, cancela eso") for p in pats["pc_cancel"])
    assert any(t.name == "job_cancel" for t in Plugin().get_tools())


def test_job_cancel_kills_running():
    import asyncio
    import time

    from plugins import pc_control as pc_mod
    from plugins.pc_control import Plugin
    p = Plugin()
    p.allow_shell = True
    started = asyncio.run(p.execute_tool("job_start", {"command": "Start-Sleep -Seconds 30", "name": "t-cancel"}))
    assert started.get("ok") is True
    jid = started["job_id"]
    try:
        time.sleep(0.5)
        res = asyncio.run(p.execute_tool("job_cancel", {"job_id": jid}))
        assert res.get("ok") is True and jid in res.get("cancelled", [])
        st = asyncio.run(p.execute_tool("job_status", {"job_id": jid}))
        assert st.get("status") == "cancelled"
    finally:
        with pc_mod._JOBS_LOCK:
            pc_mod._JOBS.pop(jid, None)
        pc_mod._PROCS.pop(jid, None)
