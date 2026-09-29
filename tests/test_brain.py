from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.brain import (
    ConversationContext,
    Intent,
    CLAUDIOBrain,
    CLAUDIOState,
    fast_first_step,
    music_query_from_text,
)
from core.memory import MemorySystem
from plugins.base import ToolSchema
from plugins.registry import PluginRegistry


@pytest.fixture
def mock_memory():
    memory = MagicMock(spec=MemorySystem)
    memory.search = AsyncMock(return_value=[])
    memory.add_conversation = AsyncMock()
    return memory


@pytest.fixture
def mock_plugin_registry():
    registry = MagicMock(spec=PluginRegistry)
    registry.get_all_tools.return_value = {}
    registry.get_all_intents.return_value = {}
    registry.get_plugin.return_value = None
    return registry


@pytest.fixture
def brain(mock_memory, mock_plugin_registry):
    with patch("core.brain.build_chat_model", return_value=MagicMock()):
        return CLAUDIOBrain(mock_memory, mock_plugin_registry)


def test_brain_initializes(brain):
    assert brain is not None
    assert brain.graph is not None


def test_state_structure():
    state: CLAUDIOState = {
        "user_input": "test",
        "intent": None,
        "context": ConversationContext(),
        "plan": [],
        "results": {},
        "response": "",
        "metadata": {},
    }
    assert state["user_input"] == "test"


def test_route_chat_vs_full(mock_memory):
    from unittest.mock import MagicMock

    from plugins.base import IntentPattern
    reg = MagicMock()
    reg.get_all_tools.return_value = {"launch_app": MagicMock()}
    reg.get_all_intents.return_value = {
        "pc_launch": IntentPattern(name="pc_launch", patterns=[r"abr[ií].*"], entities=[])
    }
    with patch("core.brain.build_chat_model", return_value=MagicMock()):
        b = CLAUDIOBrain(mock_memory, reg)
    assert b._route_after_perceive({"user_input": "hola como estas"}) == "chat"
    assert b._route_after_perceive({"user_input": "abri el notepad"}) == "full"


def test_que_paso_se_verifica_y_saludo_no():
    from core.brain import _looks_factual
    assert _looks_factual("¿Qué pasó en 9, no? 9,18? No, 9,12 Madrid?") is True
    assert _looks_factual("¿Qué pasó el 9 y el 12 en Madrid?") is True
    assert _looks_factual("Pero busca que paso el 9 de 12 en Madrid.") is True
    assert _looks_factual("qué te pasa") is False
    assert _looks_factual("qué pasó, todo bien") is False


def test_pregunta_factual_va_a_full(mock_memory):
    from unittest.mock import MagicMock, patch

    from core.brain import CLAUDIOBrain
    reg = MagicMock()
    reg.get_all_tools.return_value = {"web_search": MagicMock()}
    reg.get_all_intents.return_value = {}
    with patch("core.brain.build_chat_model", return_value=MagicMock()):
        b = CLAUDIOBrain(mock_memory, reg)
    assert b._route_after_perceive({"user_input": "cuántos habitantes tiene Córdoba"}) == "full"
    assert b._route_after_perceive({"user_input": "cómo estás"}) == "chat"
    assert b._route_after_perceive({"user_input": "cuántos años tenés"}) == "chat"


def test_split_off_sentences():
    from core.brain import _split_off_sentences
    complete, rest = _split_off_sentences("Hola Señor. Como estas")
    assert complete == ["Hola Señor."]
    assert rest == "Como estas"
    complete, rest = _split_off_sentences("Sin punto final")
    assert complete == [] and rest == "Sin punto final"


def test_quick_reply_streams_sentences(mock_memory):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    from core.brain import CLAUDIOBrain

    async def fake_stream(messages):
        for tok in ["Hola Señor. ", "Todo bien?"]:
            yield MagicMock(content=tok)

    llm = MagicMock()
    llm.astream = fake_stream
    mock_memory.search = AsyncMock(return_value=[])
    mock_memory.add_conversation = AsyncMock()
    with patch("core.brain.build_chat_model", return_value=llm):
        b = CLAUDIOBrain(mock_memory, MagicMock())
    spoken = []

    async def collect(s):
        spoken.append(s)

    resp = asyncio.run(b.quick_reply("hola", on_sentence=collect))
    assert "Hola Señor" in resp
    assert spoken and all(spoken)


def test_repeat_success_forces_done(mock_memory):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    from core.brain import ConversationContext, CLAUDIOBrain
    from plugins.base import ToolSchema

    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(
        content='{"tool": "discord_user_send", "args": {"message": "m!play X"}}'
    ))
    reg = MagicMock()
    reg.get_all_tools.return_value = {
        "discord_user_send": ToolSchema(name="discord_user_send", description="d", parameters={})
    }
    reg.get_all_intents.return_value = {}
    with patch("core.brain.build_chat_model", return_value=llm):
        b = CLAUDIOBrain(mock_memory, reg)

    async def _go():
        state = {
            "user_input": "reproducí tranqui",
            "intent": Intent(name="discord_command", confidence=0.9, entities={}),
            "context": ConversationContext(),
            "plan": [],
                "attempts": 0,
            "results": {"step_0": {"ok": True}},
            "response": "",
            "metadata": {"last_tool": {"tool": "discord_user_send", "args": {"message": "m!play X"}}},
        }
        return await b._plan(state)

    assert asyncio.run(_go())["plan"] == []


def test_repeat_equivalent_mplay_forces_done(mock_memory):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    from core.brain import ConversationContext, CLAUDIOBrain
    from plugins.base import ToolSchema

    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(
        content='{"tool": "discord_user_send", "args": {"message": "m!play https://x"}}'
    ))
    reg = MagicMock()
    reg.get_all_tools.return_value = {
        "discord_user_send": ToolSchema(name="discord_user_send", description="d", parameters={})
    }
    reg.get_all_intents.return_value = {}
    with patch("core.brain.build_chat_model", return_value=llm):
        b = CLAUDIOBrain(mock_memory, reg)

    async def _go():
        state = {
            "user_input": "ponelo de vuelta",
            "intent": Intent(name="discord_command", confidence=0.9, entities={}),
            "context": ConversationContext(),
            "plan": [],
                "attempts": 0,
            "results": {"step_0": {"ok": True}},
            "response": "",
            "metadata": {"last_tool": {"tool": "discord_user_send", "args": {"message": "m!p https://x"}}},
        }
        return await b._plan(state)

    assert asyncio.run(_go())["plan"] == []


def test_repeat_crosstool_forces_done(mock_memory):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    from core.brain import ConversationContext, CLAUDIOBrain
    from plugins.base import ToolSchema

    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(
        content='{"tool": "discord_user_send", "args": {"message": "m!play https://x"}}'
    ))
    reg = MagicMock()
    reg.get_all_tools.return_value = {
        "discord_user_send": ToolSchema(name="discord_user_send", description="d", parameters={})
    }
    reg.get_all_intents.return_value = {}
    with patch("core.brain.build_chat_model", return_value=llm):
        b = CLAUDIOBrain(mock_memory, reg)

    async def _go():
        state = {
            "user_input": "ponelo",
            "intent": Intent(name="discord_command", confidence=0.9, entities={}),
            "context": ConversationContext(),
            "plan": [],
                "attempts": 0,
            "results": {"step_0": {"ok": True}},
            "response": "",
            "metadata": {"last_tool": {"tool": "discord_command", "args": {"command": "m!p https://x"}}},
        }
        return await b._plan(state)

    assert asyncio.run(_go())["plan"] == []


def test_repeat_search_warns_and_stops(mock_memory):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    from core.brain import ConversationContext, CLAUDIOBrain
    from plugins.base import ToolSchema

    seen = {}

    async def fake_plan(messages):
        seen["prompt"] = str(messages[0].content)
        return MagicMock(content='{"done": true}')

    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=fake_plan)
    reg = MagicMock()
    reg.get_all_tools.return_value = {
        "music_search": ToolSchema(name="music_search", description="d", parameters={})
    }
    reg.get_all_intents.return_value = {}
    with patch("core.brain.build_chat_model", return_value=llm):
        b = CLAUDIOBrain(mock_memory, reg)

    async def _go():
        state = {
            "user_input": "busca ramma",
            "intent": Intent(name="music_search", confidence=0.9, entities={}),
            "context": ConversationContext(),
            "plan": [],
            "attempts": 0,
            "results": {
                "step_0": {"ok": True, "results": [{"title": "A", "url": "u1"}]},
                "step_1": {"ok": True, "results": [{"title": "B", "url": "u2"}]},
            },
            "response": "",
            "metadata": {"tool_history": ["music_search", "music_search"]},
        }
        return await b._plan(state)

    out = asyncio.run(_go())
    assert out["plan"] == []
    assert "YA BUSCASTE" in seen["prompt"]


def test_format_step_truncates():
    from core.brain import _format_step
    long_result = {"ok": True, "text": "x" * 2000}
    line = _format_step("step_0", long_result, ["browser_read"], 0)
    assert len(line) <= 300
    assert "browser_read" in line


def test_claudio_version():
    from core.brain import _claudio_version
    assert isinstance(_claudio_version(), str) and len(_claudio_version()) > 0

def test_music_query_fallback_ramma():
    assert music_query_from_text("emmm elegi una por mi, busca el link y reproducila") == "RAMMA"
    assert "RAMMA" in music_query_from_text("reproducila en una de rama en discord")


def test_fast_first_step_music():
    s = fast_first_step("elegi una por mi en 2do plano", "music_search")
    assert s is not None and s.tool == "mymusic_cached_play" and s.args["artist"] == "RAMMA"
    s = fast_first_step("pone mi tranqui", "music_search")
    assert s is not None and s.tool == "discord_user_send" and "list=" in s.args["message"]
    s = fast_first_step("m!p https://www.youtube.com/watch?v=abc123XYZ_-", "discord_command")
    assert s is not None and s.tool == "discord_user_send" and s.args["message"].startswith("m!p https")
    assert fast_first_step("hola como estas", "unknown") is None
    assert fast_first_step("hola como estas", "chat") is None


def test_normalize_spelled_claudio():
    from core.brain import normalize_user_text
    assert normalize_user_text("C.L.A.U.D.I.O. me escuchas bien") == "CLAUDIO me escuchas bien"
    assert normalize_user_text("el F.B.I. vino") == "el F.B.I. vino"


def test_normalize_soundalikes_ramma():
    from core.brain import normalize_user_text
    assert "RAMMA" in normalize_user_text("reproducir el tema de carby")


def test_local_confirm_ok_and_empty():
    from core.brain import _local_confirm
    res = {"step_0": {"ok": True, "pick": {"title": "en una"}}}
    assert _local_confirm(res, ["music_play"], ["m!p https://youtu.be/AAA"]) == "Listo, en una en segundo plano."
    assert _local_confirm(res, ["music_play"]) == "Encontré: en una. ¿Cuál pongo?"
    assert _local_confirm({}, []) == "Lo siento, tuve un problema generando la respuesta. Intenta de nuevo."
    assert _local_confirm({"step_0": {"error": "boom"}}, ["music_play"]) == "Lo siento, tuve un problema generando la respuesta. Intenta de nuevo."


def test_local_confirm_never_lies_on_search_only():
    from core.brain import _local_confirm
    res = {"step_0": {"ok": True, "results": [{"title": "X"}]}}
    out = _local_confirm(res, ["mymusic_search"])
    assert out == "Encontré: X. ¿Cuál pongo?"
    assert "segundo plano" not in out


def test_fast_first_step_top_history():
    from core.brain import fast_first_step
    s = fast_first_step("cuales son los tres temas que mas escucho en youtube music", "mymusic")
    assert s is not None and s.tool == "mymusic_history"


def test_synthesize_falls_back_to_local_confirm(brain):
    import asyncio
    from unittest.mock import AsyncMock

    from core.brain import ConversationContext
    brain.llm_main.ainvoke = AsyncMock(side_effect=Exception("429"))
    state = {
        "user_input": "pone ramma",
        "intent": None,
        "context": ConversationContext(),
        "plan": [],
        "attempts": 0,
        "results": {"step_0": {"ok": True, "pick": {"title": "en una"}}},
        "response": "",
        "metadata": {"tool_history": ["music_play"], "sent_commands": ["m!p https://youtu.be/AAA"]},
    }
    out = asyncio.run(brain._synthesize(state))
    assert out["response"] == "Listo, en una en segundo plano."


def test_fast_first_step_swap_and_otra():
    from core.brain import fast_first_step, music_query_from_text
    q = music_query_from_text("quita esa musica y coloca un Nublado de poblodondra en discord")
    assert "nublado" in q.lower() and "Paulo Londra" in q and "quit" not in q.lower()
    s = fast_first_step("otra", "music_search")
    assert s is not None and s.tool == "mymusic_cached_play" and s.args.get("artist") == "RAMMA"


def test_auto_music_sends_play_instantly(brain):
    import asyncio

    from core.brain import ConversationContext, Intent
    from plugins.base import ToolSchema
    brain.plugin_registry.get_all_tools.return_value = {"discord_user_send": ToolSchema(name="discord_user_send", description="d"), "mymusic_search": ToolSchema(name="mymusic_search", description="d")}
    state = {
        "user_input": "pone nublado de Paulo Londra en discord",
        "intent": Intent(name="music_search", confidence=0.9, entities={}),
        "context": ConversationContext(),
        "plan": [],
        "attempts": 0,
        "results": {"step_0": {"ok": True, "results": [{"title": "Nublado", "url": "https://www.youtube.com/watch?v=AAA"}]}},
        "response": "",
        "metadata": {"tool_history": ["mymusic_search"]},
    }
    out = asyncio.run(brain._plan(state))
    assert len(out["plan"]) == 1
    assert out["plan"][0].tool == "discord_user_send"
    assert out["plan"][0].args["message"] == "m!p https://www.youtube.com/watch?v=AAA"


def test_auto_music_stop_first_on_swap(brain):
    import asyncio

    from core.brain import ConversationContext, Intent
    brain.plugin_registry.get_all_tools.return_value = {"discord_user_send": ToolSchema(name="discord_user_send", description="d"), "mymusic_search": ToolSchema(name="mymusic_search", description="d"), "music_control": ToolSchema(name="music_control", description="d")}
    base = {
        "user_input": "saca esa y pone otra de ramma",
        "intent": Intent(name="music_search", confidence=0.9, entities={}),
        "context": ConversationContext(),
        "plan": [],
        "attempts": 0,
        "results": {"step_0": {"ok": True, "results": [{"title": "X", "url": "https://youtu.be/BBB"}]}},
        "response": "",
        "metadata": {"tool_history": ["mymusic_search"]},
    }
    out = asyncio.run(brain._plan(dict(base)))
    assert out["plan"][0].tool == "music_control" and out["plan"][0].args["action"] == "stop"
    base["metadata"] = {"tool_history": ["mymusic_search", "discord_user_send"], "sent_commands": ["m!stop"]}
    out = asyncio.run(brain._plan(dict(base)))
    assert out["plan"][0].args["message"] == "m!p https://youtu.be/BBB"


def test_auto_music_skips_show_only(brain):
    import asyncio
    from unittest.mock import AsyncMock

    from core.brain import ConversationContext, Intent
    brain.plugin_registry.get_all_tools.return_value = {"discord_user_send": ToolSchema(name="discord_user_send", description="d"), "mymusic_search": ToolSchema(name="mymusic_search", description="d")}
    brain.llm_fast.ainvoke = AsyncMock(side_effect=Exception("sin LLM"))
    state = {
        "user_input": "mostrame que opciones hay de ramma",
        "intent": Intent(name="music_search", confidence=0.9, entities={}),
        "context": ConversationContext(),
        "plan": [],
        "attempts": 0,
        "results": {"step_0": {"ok": True, "results": [{"title": "X", "url": "https://youtu.be/BBB"}]}},
        "response": "",
        "metadata": {"tool_history": ["mymusic_search"]},
    }
    out = asyncio.run(brain._plan(state))
    assert out["plan"] == []


def test_play_done_skips_llm(brain):
    import asyncio
    from unittest.mock import AsyncMock

    from core.brain import ConversationContext, Intent
    brain.plugin_registry.get_all_tools.return_value = {"discord_user_send": ToolSchema(name="discord_user_send", description="d")}
    brain.llm_fast.ainvoke = AsyncMock(side_effect=AssertionError("no debe llamar al LLM"))
    state = {
        "user_input": "pone ramma",
        "intent": Intent(name="music_search", confidence=0.9, entities={}),
        "context": ConversationContext(),
        "plan": [],
        "attempts": 0,
        "results": {"step_0": {"ok": True}, "step_1": {"ok": True}},
        "response": "",
        "metadata": {"tool_history": ["mymusic_search", "discord_user_send"], "sent_commands": ["m!p https://youtu.be/AAA"]},
    }
    out = asyncio.run(brain._plan(state))
    assert out["plan"] == []


def test_control_command_table():
    from core.brain import control_command
    assert control_command("podes pausar la musica del discord?") == "m!pause"
    assert control_command("limpia la música del Discord") == "m!stop"
    assert control_command("limpia todo") == "m!stop"
    assert control_command("CLAUDIO, pausa la musica en segundo plano") == "m!pause"
    assert control_command("reanuda eso") == "m!resume"
    assert control_command("dale, segui") == "m!resume"
    assert control_command("salta este tema") == "m!skip"
    assert control_command("mostra la cola") == "m!queue"
    assert control_command("claudio para") == "m!stop"
    assert control_command("alto ahi") == "m!stop"
    assert control_command("paralo ya") == "m!stop"
    assert control_command("sigue sonando?") is None
    assert control_command("pone el tema Pausa") is None
    assert control_command("hola como estas") is None


def test_fast_first_step_control_never_searches():
    from core.brain import fast_first_step
    s = fast_first_step("Podes pausar la musica que esta sonando en el Discord?", "music_search")
    assert s is not None and s.tool == "music_control" and s.args["action"] == "pause"
    s = fast_first_step("CLAUDIO, pausa la musica en segundo plano, en Discord.", "music_search")
    assert s is not None and s.tool == "music_control" and s.args["action"] == "pause"


def test_rescue_control_from_bad_search(brain):
    import asyncio

    from core.brain import ConversationContext, Intent
    brain.plugin_registry.get_all_tools.return_value = {"discord_user_send": ToolSchema(name="discord_user_send", description="d"), "music_control": ToolSchema(name="music_control", description="d")}
    state = {
        "user_input": "pausa la musica",
        "intent": Intent(name="music_search", confidence=0.9, entities={}),
        "context": ConversationContext(),
        "plan": [],
        "attempts": 0,
        "results": {"step_0": {"ok": True, "query": "pausa la musica", "results": [{"title": "X", "url": "https://youtu.be/AAA"}]}},
        "response": "",
        "metadata": {"tool_history": ["mymusic_search"]},
    }
    out = asyncio.run(brain._plan(state))
    assert len(out["plan"]) == 1 and out["plan"][0].tool == "music_control" \
        and out["plan"][0].args["action"] == "pause"


def test_local_confirm_control_words():
    from core.brain import _local_confirm
    res = {"step_0": {"ok": True}}
    assert _local_confirm(res, ["discord_user_send"], ["m!pause"]) == "Pausado."
    assert _local_confirm(res, ["discord_user_send"], ["m!resume"]) == "Siguiendo."
    assert _local_confirm(res, ["discord_user_send"], ["m!p https://x"]) == "Listo."


def test_wants_show():
    from core.brain import wants_show
    assert wants_show("mostrame que hiciste") is True
    assert wants_show("decime que esta sonando") is True
    assert wants_show("pone ramma") is False
    assert wants_show("pausa") is False


def test_query_cleans_rambling_and_known_songs():
    from core.brain import music_query_from_text
    q = music_query_from_text("podes quitar la musica, pero, osea, quitarla del disco, reproducir solo nublado")
    assert "osea" not in q and "disco" not in q and "quit" not in q.lower()
    assert "nublado" in q.lower() and "Paulo Londra" in q
    assert music_query_from_text("pone en una") == "en una RAMMA"


def test_synthesize_skips_llm_when_play_done(brain):
    import asyncio
    from unittest.mock import AsyncMock

    from core.brain import ConversationContext, Intent
    brain.llm_main.ainvoke = AsyncMock(side_effect=AssertionError("no debe llamar al LLM"))
    state = {
        "user_input": "pone nublado",
        "intent": Intent(name="music_search", confidence=0.9, entities={}),
        "context": ConversationContext(),
        "plan": [],
        "attempts": 0,
        "results": {"step_0": {"ok": True, "results": [{"title": "Nublado", "url": "https://youtu.be/AAA"}]}, "step_1": {"ok": True}},
        "response": "",
        "metadata": {"tool_history": ["mymusic_search", "discord_user_send"], "sent_commands": ["m!p https://youtu.be/AAA"]},
    }
    out = asyncio.run(brain._synthesize(state))
    assert out["response"] == "Listo, Nublado en segundo plano."


def test_control_done_skips_llm(brain):
    import asyncio
    from unittest.mock import AsyncMock

    from core.brain import ConversationContext, Intent
    brain.plugin_registry.get_all_tools.return_value = {"music_control": ToolSchema(name="music_control", description="d")}
    brain.llm_fast.ainvoke = AsyncMock(side_effect=AssertionError("no debe llamar al LLM"))
    brain.llm_main.ainvoke = AsyncMock(side_effect=AssertionError("no debe llamar al LLM"))
    base = {
        "user_input": "pausa",
        "intent": Intent(name="discord_command", confidence=0.97, entities={}),
        "context": ConversationContext(),
        "plan": [],
        "attempts": 0,
        "results": {"step_0": {"ok": True, "action": "pause", "command": "m!pause"}},
        "response": "",
        "metadata": {"tool_history": ["music_control"], "sent_commands": ["m!pause"]},
    }
    out = asyncio.run(brain._plan(dict(base)))
    assert out["plan"] == []
    out = asyncio.run(brain._synthesize(dict(base)))
    assert out["response"] == "Pausado."


def test_swap_verbs_match():
    from core.brain import _SWAP_RE
    yes = ["quita todo", "quitar la musica", "limpia todo", "limpiar la cola", "elimina eso", "borra todo", "clear", "saca esa y pone otra", "para la musica", "corta eso"]
    no = ["para que busques algo", "poneme cumbia", "hola"]
    for s in yes:
        assert _SWAP_RE.search(s), s
    for s in no:
        assert not _SWAP_RE.search(s), s


def test_fast_first_step_swap_direct_stop():
    from core.brain import fast_first_step
    for s in ["quita todas las musicas del discord", "limpia todo", "borra todo", "elimina eso"]:  
        st = fast_first_step(s, "music_search")
        assert st is not None and st.tool == "music_control" and st.args["action"] == "stop", s
    st = fast_first_step("quita esa y pone Nublado de Paulo Londra", "music_search")
    assert st is not None and st.tool == "mymusic_search" and "Nublado" in st.args["query"]


def test_fast_first_step_replay_last():
    from core.brain import fast_first_step
    s = fast_first_step("Reproduce de vuelta la musica en Discord.", "music_search")
    assert s is not None and s.tool == "music_replay"
    s = fast_first_step("de vuelta", "music_search")
    assert s is not None and s.tool == "music_replay"
    s = fast_first_step("pone otra vez Nublado", "music_search")
    assert s is not None and s.tool == "mymusic_search" and "Nublado" in s.args["query"]


def test_music_replay_tool(tmp_path):
    import asyncio
    import json

    from core.store import Store
    from plugins.music import Plugin
    p = Plugin()
    p.store = Store(tmp_path)
    assert "error" in asyncio.run(p.execute_tool("music_replay", {}))
    p.store.log_event("musica", json.dumps({"title": "Nublado", "url": "https://youtu.be/AAA"}))
    res = asyncio.run(p.execute_tool("music_replay", {}))
    assert res.get("ok") is True and res["pick"]["url"] == "https://youtu.be/AAA"


def test_stop_now_excludes_otra_and_para_tarea():
    from core.brain import _STOP_NOW_RE, fast_first_step
    assert not _STOP_NOW_RE.search("para la tarea")
    assert not _STOP_NOW_RE.search("para que busques algo")
    assert _STOP_NOW_RE.search("para la musica")
    s = fast_first_step("otra", "music_search")
    assert s is not None and s.tool == "mymusic_cached_play"


def test_orders_loaded_into_planner():
    from core.brain import load_orders
    txt = load_orders()
    assert "RAMMA" in txt and "m!pause" in txt and len(txt) <= 1800


def test_wants_reset():
    from core.brain import wants_reset
    assert wants_reset("empeza de cero") is True
    assert wants_reset("limpia el contexto y seguimos") is True
    assert wants_reset("pone ramma") is False


def test_fase4_routing_audit(brain):
    from unittest.mock import MagicMock

    from plugins.discord import Plugin as DiscordPlugin
    from plugins.music import Plugin as MusicPlugin
    from plugins.pc_control import Plugin as PcPlugin
    intents = {}
    for p in (PcPlugin(), MusicPlugin(), DiscordPlugin()):
        for i in p.get_intents():
            intents[i.name] = i
    reg = MagicMock()
    reg.get_all_tools.return_value = {"x": MagicMock()}
    reg.get_all_intents.return_value = intents
    brain.plugin_registry = reg
    route = brain._route_after_perceive
    assert route({"user_input": "hola"}) == "chat"
    assert route({"user_input": "gracias"}) == "chat"
    assert route({"user_input": "che abrime el discord"}) == "full"
    assert route({"user_input": "pone algo de RAMMA"}) == "full"
    assert route({"user_input": "pausa"}) == "full"
    assert route({"user_input": "cerralo"}) == "full"
    assert route({"user_input": "Mentira, no lo hiciste"}) == "chat"


def test_process_reset_rotates_thread(brain):
    import asyncio
    seen = {}
    async def fake_ainvoke(state, config):
        seen["thread"] = config["configurable"]["thread_id"]
        return {"response": "hola"}
    brain.graph.ainvoke = fake_ainvoke
    asyncio.run(brain.process("empeza de cero"))
    assert seen["thread"] != "default" and "nuevo" in seen["thread"]
    asyncio.run(brain.process("hola"))
    assert seen["thread"] == "default"


def test_renovar_es_reanudar():
    from core.brain import control_command, normalize_user_text
    assert normalize_user_text("renovar la musica") == "reanudar la musica"
    assert control_command("renovar eso") == "m!resume"


def test_fast_first_step_whole_playlist(monkeypatch):
    import core.mymusic_cache as mcmod
    from core.brain import fast_first_step
    monkeypatch.setattr(mcmod, "find_playlist",
                        lambda q, vault=None: {"title": "tranqui", "url": "https://x/playlist?list=ABC"} if "tranqui" in q.lower() else None)
    s = fast_first_step("Puedes poner la playlist de Tranqui en el Discord.", "music_search")
    assert s is not None and s.tool == "discord_user_send"
    assert s.args["message"].startswith("m!p https") and "list=" in s.args["message"]
    s = fast_first_step("pone un tema de tranqui", "music_search")
    assert s is not None and s.tool == "mymusic_cached_play"


def test_fast_first_step_unknown_playlist_lists(monkeypatch):
    import core.mymusic_cache as mcmod
    from core.brain import fast_first_step
    monkeypatch.setattr(mcmod, "find_playlist", lambda q, vault=None: None)
    s = fast_first_step("pone una de mis playlists", "music_search")
    assert s is not None and s.tool == "mymusic_playlists"


def test_playlist_mia_vs_afuera(monkeypatch):
    import core.mymusic_cache as mcmod
    from core.brain import _playlist_name, _wants_outside_playlists, fast_first_step
    assert _wants_outside_playlists("buscá una playlist de Milo J") is True
    assert _wants_outside_playlists("pone una playlist de rock") is True
    assert _wants_outside_playlists("pone la playlist pop") is False
    assert _wants_outside_playlists("pone mi playlist tranqui") is False
    assert _wants_outside_playlists("busca mi playlist de trap") is False
    assert _playlist_name("buscá una playlist de Milo J") == "milo j"
    assert _playlist_name("poné la playlist pop") == "pop"
    assert _playlist_name("pone mi playlist tranqui en discord") == "tranqui"
    monkeypatch.setattr(mcmod, "find_playlist",
                        lambda q, vault=None: {"title": "pop", "url": "https://music.youtube.com/playlist?list=PLPOP"}
                        if "pop" in q.lower() else None)
    s = fast_first_step("poné la playlist pop", "music_search")
    assert s is not None and s.tool == "discord_user_send"
    assert s.args["message"] == "m!p https://music.youtube.com/playlist?list=PLPOP"
    s = fast_first_step("buscá una playlist de Milo J", "music_search")
    assert s is not None and s.tool == "search_playlists"
    assert s.args["query"] == "milo j"


def test_first_link_acepta_playlist():
    from core.brain import _first_link
    res = {"step_0": {"ok": True, "results": [
        {"title": "Mix Milo J", "url": "https://music.youtube.com/playlist?list=PLAAA"}]}}
    assert _first_link(res, "busca una playlist de Milo J") == \
        "https://music.youtube.com/playlist?list=PLAAA"
    res2 = {"step_0": {"ok": True, "results": [
        {"title": "Tema", "url": "https://www.youtube.com/watch?v=BBBBBBBBBBB"},
        {"title": "Mix", "url": "https://music.youtube.com/playlist?list=PLAAA"}]}}
    assert _first_link(res2, "x") == "https://www.youtube.com/watch?v=BBBBBBBBBBB"
    vacio = {"step_0": {"ok": True, "results": [
        {"title": "Chakras", "url": "https://www.youtube.com/watch?v="}]}}
    assert _first_link(vacio, "musica para limpiar") == ""


def test_cancel_no_toca_olvidados():
    from core.brain import _cancel_step, fast_first_step
    tools = {"job_cancel": object()}
    assert _cancel_step("poné favoritos olvidados", tools) is None
    assert _cancel_step("olvidalo por ahora", tools).tool == "job_cancel"
    s = fast_first_step("poné alguna música de mis favoritos olvidados", "music_search")
    assert s is not None and s.tool == "mymusic_forgotten"


def test_esquipear_con_e_y_q():
    from core.brain import control_command, fast_first_step, fast_path_intent
    assert control_command("te faltó esquipear la música, esquipeala") == "m!skip"
    assert control_command("mi equipo juega hoy") is None
    assert fast_path_intent("esquipea la música").name == "discord_command"
    s = fast_first_step("CLAUDIO esquipeá la música", "music_search")
    assert s is not None and s.tool == "music_control" and s.args["action"] == "skip"


def test_skipear_es_skip_sin_buscar():
    from core.brain import control_command, fast_first_step
    assert control_command("podes skipear la musica que esta sonando") == "m!skip"
    s = fast_first_step("CLAUDIO skipea la música de Discord", "music_search")
    assert s is not None and s.tool == "music_control" and s.args["action"] == "skip"


def test_gonna_fly_now_canon():
    from core.brain import music_query_from_text
    q = music_query_from_text("ese tema You wanna split now de la película de Rocky")
    assert "Gonna Fly Now" in q and "Bill Conti" in q


def test_hora_fecha_sin_llm():
    from core.brain import _is_pure_time_question, _local_time_answer, _system_time_step, fast_path_intent
    assert fast_path_intent("qué hora es").name == "system_time"
    assert fast_path_intent("decime la fecha de hoy").name == "system_time"
    assert _is_pure_time_question("qué hora es") is True
    assert _is_pure_time_question("qué hora es en Tokio") is False
    assert _is_pure_time_question("poné música") is False
    st = _system_time_step("system_time", {"system_time": object()})
    assert st is not None and st.tool == "system_time"
    assert _system_time_step("music_search", {"system_time": object()}) is None
    res = {"step_0": {"ok": True, "fecha": "viernes 18 de septiembre de 2026",
                      "hora": "00:59", "iso": "2026-09-18T00:59:54"}}
    assert _local_time_answer("qué hora es", res) == "Son las 00:59."
    assert _local_time_answer("qué fecha es hoy", res) == "Hoy es viernes 18 de septiembre de 2026."
    assert _local_time_answer("qué hora es en Tokio", res) == ""


def test_retrieve_se_salta_en_musica_deterministica():
    import asyncio
    from types import SimpleNamespace

    from core.brain import ConversationContext, Intent, CLAUDIOBrain
    llamadas = []

    async def _search(q, top_k=3):
        llamadas.append(q)
        return []

    async def _get_relevant_context(q, limit=2):
        return []

    tools = {"music_control": object(), "mymusic_search": object(),
             "discord_user_send": object(), "mymusic_playlists": object()}
    yo = SimpleNamespace(memory=SimpleNamespace(search=_search, get_relevant_context=_get_relevant_context),
                         plugin_registry=SimpleNamespace(get_all_tools=lambda: tools))

    def _st(text, intent):
        return {"intent": Intent(name=intent, confidence=0.9, entities={}),
                "user_input": text, "context": ConversationContext()}

    asyncio.run(CLAUDIOBrain._retrieve(yo, _st("CLAUDIO skipea la música", "music_search")))
    assert llamadas == []
    asyncio.run(CLAUDIOBrain._retrieve(yo, _st("qué hora es", "general")))
    assert len(llamadas) == 1


def test_followup_playlist_random_y_limpieza():
    from core.brain import _playlist_followup, auto_music_step

    class FakeIntent:
        def __init__(self, n): self.name = n

    def st(sent):
        return {"intent": FakeIntent("mymusic"), "user_input": "poné la playlist pop",
                "results": {}, "metadata": {"tool_history": [], "sent_commands": list(sent)}}

    s = _playlist_followup(st(["m!p https://music.youtube.com/playlist?list=PLX"]))
    assert s is not None and s.args["message"] == "m!select random"
    s = _playlist_followup(st(["m!p https://music.youtube.com/playlist?list=PLX", "m!select random"]))
    assert s is not None and s.args["message"] == "m!remove after played"
    assert _playlist_followup(st(["m!p https://music.youtube.com/playlist?list=PLX",
                                  "m!select random", "m!remove after played"])) is None
    # Tema suelto: nada. Stop despues: nada.
    assert _playlist_followup(st(["m!p https://www.youtube.com/watch?v=AAA11111111"])) is None
    assert _playlist_followup(st(["m!p https://music.youtube.com/playlist?list=PLX", "m!stop"])) is None
    # Integrado: despues del m!p el auto-chain manda select.
    st2 = st(["m!p https://music.youtube.com/playlist?list=PLX"])
    st2["results"] = {"step_0": {"ok": True}}
    step = auto_music_step(st2)
    assert step is not None and step.args["message"] == "m!select random"


def test_apply_official_sync():
    from core.mymusic_cache import apply_official_sync
    out = apply_official_sync({"playlists": [], "liked": [], "tranqui_tracks": []},
                              {"ok": True, "channel": "mi-canal",
                               "playlists": [{"title": "pop", "url": "https://x/playlist?list=PLPOP"}],
                               "tracks": {"PLPOP": {"title": "pop", "items": [
                                   {"title": "T", "artists": "A", "url": "https://youtu.be/AAA"}]},
                                           "PLL": {"title": "like", "items": [
                                   {"title": "L", "artists": "B", "url": "https://youtu.be/BBB"}]}}})
    assert [p["title"] for p in out["playlists"]] == ["pop"]
    assert out["liked"][0]["url"] == "https://youtu.be/BBB"
    assert apply_official_sync({"a": 1}, {"ok": False}) == {"a": 1}


def test_pauza_con_z_y_villegru():
    from core.brain import control_command, fast_first_step, fast_path_intent
    assert control_command("Pauza la música") == "m!pause"
    assert fast_path_intent("Pauza la música").name in ("discord_command", "music_search")
    s = fast_first_step("Pauza la música", "music_search")
    assert s is not None and s.tool == "music_control" and s.args["action"] == "pause"
    import core.mymusic_cache as mc
    from core.mymusic_cache import find_playlist
    real = mc.load_cache
    mc.load_cache = lambda vault=None: {"playlists": [
        {"title": "de Villeros", "url": "https://music.youtube.com/playlist?list=PLV"}]}
    try:
        from core.brain import normalize_user_text
        assert "villero" in normalize_user_text("poné la playlist de Villegru")
        assert find_playlist("villero")["url"].endswith("PLV")
    finally:
        mc.load_cache = real


def test_misma_resuelve_y_version_se_excluye(tmp_path, monkeypatch):
    import json

    import core.config as cfgmod
    from core.brain import _resolve_misma, excluded_words, fast_path_intent
    from core.store import Store
    monkeypatch.setattr(cfgmod.settings.memory, "vault_path", tmp_path, raising=False)
    Store(tmp_path / "data").log_event("musica", json.dumps(
        {"title": "caracara", "artists": "RAMMA", "url": "https://youtu.be/AAA"}))
    out = _resolve_misma("poné esa misma música pero sin en vivo")
    assert "caracara" in out.lower() and "misma" not in out.lower()
    assert "en vivo" in [w.lower() for w in excluded_words("ponelo pero sin en vivo")]
    assert "en vivo" in [w.lower() for w in excluded_words("cara a cara pero no en vivo")]
    assert fast_path_intent("buscá el último partido de River y Boca").name == "web_search"


def test_no_pone_otra_playlist_que_la_pedida():
    from core.brain import (_local_confirm, _playlist_name_in_results,
                            auto_music_step, normalize_user_text)
    from core.brain import Intent
    res = {"step_0": {"ok": True, "playlists": [
        {"title": "like", "url": "https://music.youtube.com/playlist?list=AAA"},
        {"title": "de Villeros", "url": "https://music.youtube.com/playlist?list=BBB"}]}}
    t = "Shari, reposi la playlist pop en el Discord."
    assert "reproducila" in normalize_user_text(t).lower()
    assert "shari" not in normalize_user_text(t).lower()
    assert _playlist_name_in_results("pop", res) is False
    assert _playlist_name_in_results("villeros con doble l", res) is True
    mk = lambda txt: {"intent": Intent(name="discord_command", confidence=0.9, entities={}),
                      "user_input": txt, "results": res,
                      "metadata": {"tool_history": ["mymusic_playlists"]},
                      "plan": [], "attempts": 0}
    # "pop" no esta: NO se manda like (antes sonaba like).
    assert auto_music_step(mk(t)) is None
    # "Villeros" si: se manda BBB (no AAA) y se confirma con su nombre.
    s = auto_music_step(mk("La playlist de Villeros, con doble L."))
    assert s is not None and "list=BBB" in s.args["message"]
    c = _local_confirm(res, ["mymusic_playlists", "discord_user_send"],
                       ["m!p https://music.youtube.com/playlist?list=BBB"], "x")
    assert c == "Listo, de Villeros en segundo plano."


def test_reproduce_alguna_playlist_la_pone():
    from core.brain import (_first_playlist, _local_confirm, _playlist_unnamed,
                            auto_music_step, fast_path_intent)
    from core.brain import Intent
    res = {"step_0": {"ok": True, "playlists": [
        {"title": "like", "url": "https://music.youtube.com/playlist?list=AAA"},
        {"title": "de Villeros", "url": "https://music.youtube.com/playlist?list=BBB"}]}}
    assert _first_playlist(res) == ("like", "https://music.youtube.com/playlist?list=AAA")
    assert _first_playlist({}) == ("", "")
    t = "CLAUDIO, ¿Reproduce alguna de mis playlist en el Liscord?"
    assert _playlist_unnamed(t) is True
    assert _playlist_unnamed("poné la playlist pop") is False
    assert _playlist_unnamed("cuáles son mis playlists?") is True
    st = {"intent": Intent(name="music_search", confidence=0.9, entities={}),
          "user_input": t, "results": res,
          "metadata": {"tool_history": ["mymusic_playlists"]},
          "plan": [], "attempts": 0}
    step = auto_music_step(st)
    assert step is not None and step.tool == "discord_user_send"
    assert "list=AAA" in step.args["message"]
    # Pregunta de solo-ver: no auto-manda, y la confirm no miente "Listo".
    st2 = dict(st, user_input="cuáles son mis playlists?")
    assert auto_music_step(st2) is None
    c = _local_confirm(res, ["mymusic_playlists"], (), st2["user_input"])
    assert "Listo" not in c and "like" in c
    # Con nombre puntual no dispara el "alguna".
    st3 = dict(st, user_input="poné la playlist pop")
    r3 = auto_music_step(st3)
    assert r3 is None or "list=AAA" not in str(getattr(r3, "args", ""))


def test_no_en_vivo_se_busca_limpio_y_se_salta():
    from core.brain import _first_link, excluded_words, fast_path_intent, music_query_from_text
    t = "CLAUDIO, quita la música y pone Algún día de RAMMA pero no en vivo"
    assert excluded_words(t) == ["en vivo"]
    q = music_query_from_text(t)
    assert "en vivo" not in q.lower() and "pero no" not in q.lower()
    assert q.startswith("Algún día"), q
    mk = lambda title, url: {"title": title, "artists": "RAMMA", "url": url}
    res = {"step_0": {"ok": True, "results": [
        mk("algún día (En Vivo) (desde el Estadio Malvinas Argentinas)",
           "https://www.youtube.com/watch?v=ENVIVO12345"),
        mk("algún día", "https://www.youtube.com/watch?v=STUDIO67890")]}}
    b_first = _first_link(res, t)
    assert b_first
    assert "STUDIO" in b_first
    # Todo vetado: no se manda lo prohibido (falsy, el llamante avisa).
    res_vet = {"step_0": {"ok": True, "results": [
        mk("algún día (En Vivo)", "https://www.youtube.com/watch?v=AAA")]}}
    assert not _first_link(res_vet, t)
    # "oficial, video" prefiere Official Video.
    res_of = {"step_0": {"ok": True, "results": [
        mk("algún día (En Vivo)", "https://www.youtube.com/watch?v=AAA"),
        mk("Algún Día (Official Video)", "https://www.youtube.com/watch?v=BBB")]}}
    assert "BBB" in _first_link(res_of, "pone algún día oficial video")
    # Sin verbo igual va a musica; preguntas no.
    assert fast_path_intent("quita la música y pone Algún día pero no en vivo").name == "music_search"
    assert fast_path_intent("este tema es en vivo?") is None


def test_musica_de_lista_rota_y_no_mentir_listo():
    from core.brain import fast_path_intent, music_query_from_text, normalize_user_text
    # El turno real: "Música de Rackynolds, Oro beso, Nostalgia".
    t = "Música de Rackynolds, Oro beso, Nostalgia"
    n = normalize_user_text(t)
    assert "Rackynolds" not in n and "Oro beso" not in n
    assert "Gonna Fly Now" in n and "Otro beso" in n and "Nostalgia" in n
    assert fast_path_intent(t).name == "music_search"
    q = music_query_from_text(t)
    assert "Gonna Fly Now" in q and "otro beso" in q.lower()
    assert "Música de" not in q and "Rackynolds" not in q
    # "Otro beso" no lo rompe el filler ("otro" es muletilla).
    q2 = music_query_from_text("poné otro beso")
    assert "otro beso" in q2.lower() and "RAMMA" in q2
    assert music_query_from_text("Música") == "RAMMA"
    # Preguntas/opiniones/insultos con "música" siguen a chat.
    assert fast_path_intent("qué música es esta") is None
    assert fast_path_intent("esta música es hermosa") is None
    assert fast_path_intent("música de mierda") is None
    assert fast_path_intent("hablame de la nostalgia") is None


def test_retrieve_memoria_en_paralelo():
    import asyncio
    import time
    from types import SimpleNamespace

    from core.brain import ConversationContext, Intent, CLAUDIOBrain

    async def _slow_search(q, top_k=3):
        await asyncio.sleep(0.2)
        return []

    async def _slow_ctx(q, limit=2):
        await asyncio.sleep(0.2)
        return []

    yo = SimpleNamespace(
        memory=SimpleNamespace(search=_slow_search, get_relevant_context=_slow_ctx),
        plugin_registry=SimpleNamespace(get_all_tools=lambda: {}),
    )
    st = {"intent": Intent(name="general", confidence=0.9, entities={}),
          "user_input": "contame algo interesante",
          "context": ConversationContext(), "metadata": {}}
    t0 = time.perf_counter()
    asyncio.run(CLAUDIOBrain._retrieve(yo, st))
    dt = time.perf_counter() - t0
    assert dt < 0.35, f"secuencial? {dt:.2f}s"


def test_prune_tools_achica_prompt():
    from types import SimpleNamespace

    from core.brain import _prune_tools_for
    tools = {n: SimpleNamespace(description=n, parameters={}) for n in
             ("mymusic_search", "discord_user_send", "music_control", "job_cancel",
              "web_search", "browser_open", "run_shell", "launch_app", "speak")}
    mus = _prune_tools_for("music_search", tools, "poné RAMMA")
    assert set(mus) == {"mymusic_search", "discord_user_send", "music_control", "job_cancel"}
    web = _prune_tools_for("web_search", tools, "resultado Boca River")
    assert set(web) == {"web_search", "browser_open", "job_cancel"}
    assert _prune_tools_for("unknown", tools, "hola") is tools
    assert _prune_tools_for("music_search", tools, "abrí el navegador") is tools
    assert _prune_tools_for("music_search", {}, "x") == {}


def test_final_copa_va_a_web_y_tema_final_a_musica():
    from core.brain import _looks_factual, fast_path_intent
    assert fast_path_intent("la final de Madrid del 9-12 la conocés").name == "web_search"
    assert fast_path_intent("quién ganó la copa libertadores 2018").name == "web_search"
    assert fast_path_intent("poné el tema Final de RAMMA").name == "music_search"
    # Hábito de verificar (Madrid): factuales van al circuito completo.
    assert _looks_factual("cuántos habitantes tiene Córdoba") is True
    assert _looks_factual("cuál es la capital de Francia") is True
    assert _looks_factual("por qué el cielo es azul") is True
    assert _looks_factual("cómo estás") is False
    assert _looks_factual("qué hacés") is False
    assert _looks_factual("cuántos años tenés") is False
    assert _looks_factual("quién sos") is False
    assert _looks_factual("quién") is False
    assert _looks_factual("poné RAMMA") is False


def test_busca_solo_no_es_musica_y_web_es_directa():
    from core.brain import fast_path_intent
    # El hueco del Boca-Racing: "busca ... el rato de Boca" sin companero -> NO musica.
    assert fast_path_intent("busca ahora cual fue el rato de Boca hoy") is None
    assert fast_path_intent("buscá el resultado de Ipuka hoy").name == "web_search"
    assert fast_path_intent("quién ganó ayer").name == "web_search"
    assert fast_path_intent("cómo no tenés el resultado de hoy").name == "web_search"
    # Con companero de musica sigue siendo musica.
    assert fast_path_intent("buscá RAMMA").name == "music_search"
    assert fast_path_intent("busca el tema mas reproducido").name == "music_search"
    assert fast_path_intent("buscá una playlist de Milo J").name == "music_search"
    # Preguntas de musica con verbo de poner no se van a web.
    assert fast_path_intent("poné el tema de Rocky").name == "music_search"


def test_rocky_racky_es_gonna_fly_now():
    from core.brain import fast_path_intent, music_query_from_text, normalize_user_text
    # "Racky" (STT con K) = el tema de Rocky, no una persona desconocida.
    assert "Gonna Fly Now" in normalize_user_text("Más de Racky")
    assert "Gonna Fly Now" in normalize_user_text("poné Rocky")
    assert "Racky" not in normalize_user_text("Más de Racky")
    assert music_query_from_text("Más de Racky") == "Gonna Fly Now Bill Conti"
    assert "más" not in music_query_from_text("Más de Racky").lower()
    # Sin verbo igual van a musica (antes caian a chat: "no tengo información").
    assert fast_path_intent("más de rocky").name == "music_search"
    assert fast_path_intent("más de racky").name == "music_search"
    assert fast_path_intent("eye of the tiger").name == "music_search"
    # Web y preguntas intactas.
    assert fast_path_intent("buscá el último partido de River y Boca").name == "web_search"


def test_saja_b_truncado_y_artista_pelado():
    from core.brain import _query_artist, fast_path_intent, normalize_user_text
    # Truncado "Saja B" (se corta "boys" al soltar la tecla) = Saja Boys.
    assert normalize_user_text("Saja B") == "Saja Boys"
    assert "Saja Boys" in normalize_user_text("pone saja b en discord")
    # Guardaespaldas: "la saga Batman/Bond" NO es Saja Boys.
    assert "Saja Boys" not in normalize_user_text("pone la saga Batman")
    assert _query_artist("pone saja b") == "Saja Boys"
    # Artista pelado = PONER musica, no describir.
    assert fast_path_intent("Saja B").name == "music_search"
    assert fast_path_intent("Saja Boys").name == "music_search"
    assert fast_path_intent("RAMMA").name == "music_search"
    assert fast_path_intent("Paulo Londra").name == "music_search"
    # Preguntas de conocimiento van a web (no se inventa bio); opiniones a chat.
    assert fast_path_intent("quién es Saja Boys").name == "web_search"
    assert fast_path_intent("me gusta RAMMA") is None


def test_tartamudeo_y_villero_y_aclaracion():
    from core.brain import _wants_playlist_link, auto_music_step, normalize_user_text
    assert "reanuda" in normalize_user_text("Cárguer, re-re-re-anuda la música")
    assert "rock-and-roll" in normalize_user_text("amo el rock-and-roll").lower()
    assert "villero" in normalize_user_text("la playlist de Vichero").lower()
    assert _wants_playlist_link("La playlist de Villeros, con doble L.") is True
    assert _wants_playlist_link("buscá una playlist de Milo J") is False
    assert _wants_playlist_link("cuáles son mis playlists?") is False
    assert _wants_playlist_link("decime las playlists pero no las pongas") is False

    class FakeIntent:
        def __init__(self, n): self.name = n
    st = {"intent": FakeIntent("mymusic"),
          "user_input": "La playlist de Villeros, con doble L.",
          "results": {"step_0": {"ok": True, "results": [
              {"title": "de Villeros", "url": "https://music.youtube.com/playlist?list=PLV"}]}},
          "metadata": {"tool_history": ["search_playlists"]}}
    step = auto_music_step(st)
    assert step is not None and step.tool == "discord_user_send"
    assert step.args["message"] == "m!p https://music.youtube.com/playlist?list=PLV"


def test_artista_e_idioma_en_pick():
    from core.brain import _first_link, _query_artist, excluded_words
    assert _query_artist("soda pop en ingles de los saga boys") == "Saja Boys"
    assert _query_artist("poné nublado") == ""
    assert "spanish" in [w.lower() for w in excluded_words("soda pop pero no en español")]
    r = {"step_0": {"ok": True, "results": [
        {"title": "Soda Pop (Spanish)", "artists": "Saja Boys",
         "url": "https://www.youtube.com/watch?v=AAA11111111"},
        {"title": "Soda Pop", "artists": "Saja Boys",
         "url": "https://www.youtube.com/watch?v=BBB22222222"}]}}
    assert _first_link(r, "soda pop en ingles de los saga boys") == \
        "https://www.youtube.com/watch?v=BBB22222222"
    r2 = {"step_0": {"ok": True, "results": [
        {"title": "Mix raro", "artists": "Otro",
         "url": "https://www.youtube.com/watch?v=CCC33333333"},
        {"title": "En una", "artists": "RAMMA",
         "url": "https://www.youtube.com/watch?v=DDD44444444"}]}}
    assert _first_link(r2, "poné algo de RAMMA") == "https://www.youtube.com/watch?v=DDD44444444"


def test_quipi_y_charris_y_lista():
    from core.brain import _playlist_name, control_command, fast_first_step, fast_path_intent
    from core.call import _extract_command
    assert control_command("a quipi a la música") == "m!skip"
    assert control_command("mi equipo juega hoy") is None
    found, cmd = _extract_command("Charris decime mis playlists")
    assert found is True and cmd == "decime mis playlists"
    assert fast_path_intent("mostrame la lista completa de mis playlists").name == "music_search"
    s = fast_first_step("mostrame la lista completa de mis playlists", "music_search")
    assert s is not None and s.tool == "mymusic_playlists"
    assert _playlist_name("x") == ""


def test_album_pide_disco_completo():
    from core.brain import fast_first_step, fast_path_intent
    assert fast_path_intent("poné algún álbum de Paulo Londra").name == "music_search"
    s = fast_first_step("poné algún álbum de Paulo Londra", "music_search")
    assert s is not None and s.tool == "mymusic_album"
    assert "paulo" in s.args["query"].lower()


def test_fast_intent_playlist():
    from core.brain import fast_path_intent
    assert fast_path_intent("buscá una playlist de Milo J").name == "music_search"
    assert fast_path_intent("poné la playlist pop").name == "music_search"


def test_pregunta_playlist_lista_no_manda(monkeypatch):
    import core.mymusic_cache as mcmod
    from core.brain import _is_correction, auto_music_step, fast_first_step
    monkeypatch.setattr(mcmod, "find_playlist",
                        lambda q, vault=None: {"title": "x", "url": "https://music.youtube.com/playlist?list=PLX"})
    s = fast_first_step("cuáles son mis playlists que tenés registradas", "mymusic")
    assert s is not None and s.tool == "mymusic_playlists"
    s = fast_first_step("cuáles son las pelis registradas, pero ahora no lo hagas", "mymusic")
    assert s is not None and s.tool == "mymusic_playlists"
    # Negacion gana aunque haya verbo de poner: el encadenado no manda m!p.
    st = {"intent": type("I", (), {"name": "mymusic"})(),
          "user_input": "decime las playlists pero no las pongas todavía",
          "results": {"step_0": {"ok": True, "results": [
              {"title": "Mix", "url": "https://music.youtube.com/playlist?list=PLX"}]}},
          "metadata": {"tool_history": ["mymusic_playlists"]}}
    assert auto_music_step(st) is None
    assert _is_correction("Mentira, no lo hiciste.") is True
    assert _is_correction("Mentira, mandale eso a Juan por discord.") is False
    assert _is_correction("poné RAMMA en discord") is False


def test_find_playlist_tokens():
    import core.mymusic_cache as mc
    from core.mymusic_cache import find_playlist
    real = mc.load_cache
    mc.load_cache = lambda vault=None: {"playlists": [
        {"title": "TikTok Songs", "url": "https://music.youtube.com/playlist?list=PLT"},
        {"title": "PELIS DE MIERDA", "url": "https://music.youtube.com/playlist?list=PLP"},
        {"title": "pop", "url": "https://music.youtube.com/playlist?list=PLPOP"}]}
    try:
        # "son" ya NO matchea "songs" (era el falso positivo de TikTok Songs)
        assert find_playlist("cuáles son mis playlists registradas") is None
        assert find_playlist("pop")["url"].endswith("PLPOP")
        assert find_playlist("pelis")["url"].endswith("PLP")
    finally:
        mc.load_cache = real


def test_find_playlist_tranqui_always(tmp_path):
    from core.mymusic_cache import find_playlist
    hit = find_playlist("tranqui", vault=tmp_path)
    assert hit and "list=" in hit["url"]
    assert find_playlist("zzzinexistente", vault=tmp_path) is None


def test_extract_memory_commands():
    from core.brain import extract_memory_commands
    fs = extract_memory_commands("Cuando te diga que reproduzcas una playlist, reproduzis la playlist completa. Eso guardalo en tu memoria.")
    assert any("playlist completa" in f for f in fs)
    fs = extract_memory_commands("guardalo en tu memoria: me gusta el trap")
    assert any("trap" in f for f in fs)
    assert extract_memory_commands("pone ramma") == []


def test_skillet_and_spelled_join():
    from core.brain import normalize_user_text
    assert normalize_user_text("una de skyshet") == "una de Skillet"
    assert normalize_user_text("S-K-I-L-E-T es rock") == "Skillet es rock"
    assert "rock-and-roll" in normalize_user_text("amo el rock-and-roll").lower()


def test_banda_ref_uses_last_artist(tmp_path, monkeypatch):
    import core.config as cfgmod
    from core import brain as bmod
    from core.brain import fast_first_step
    monkeypatch.setattr(cfgmod.settings.memory, "vault_path", tmp_path, raising=False)
    bmod.remember_artist("Skillet")
    s = fast_first_step("de esa banda pone la mas famosa", "music_search")
    assert s is not None and "Skillet" in s.args.get("query", "")


def test_pending_action_keeps_going():
    from core.brain import _has_pending_action, deterministic_done
    st = {"user_input": "quita todo y pone cumbia", "results": {"step_0": {"ok": True}}, "metadata": {"tool_history": ["music_control"], "sent_commands": ["m!stop"]}}
    assert _has_pending_action(st["user_input"]) is True
    assert deterministic_done(st) is False
    st2 = {"user_input": "frena", "results": {"step_0": {"ok": True}}, "metadata": {"tool_history": ["music_control"], "sent_commands": ["m!stop"]}}
    assert deterministic_done(st2) is True


def test_rise_skillet_variants():
    from core.brain import music_query_from_text, normalize_user_text
    assert "Skillet" in normalize_user_text("una de skidget")
    assert "Skillet" in normalize_user_text("temas de kitschets")
    assert music_query_from_text("reproduce raze o monster") == "Rise o Monster" or "Rise" in music_query_from_text("reproduce raze o monster")
    q = music_query_from_text("no, quise decir Nublado")
    assert "Nublado" in q and "quise" not in q.lower()


def test_fase1_chat_prompt_base(brain):
    import asyncio
    from unittest.mock import MagicMock

    from core.brain import ConversationContext
    seen = {}
    async def fake_ainvoke(messages):
        seen["system"] = messages[0].content
        return MagicMock(content="ok")
    brain.llm_main.ainvoke = fake_ainvoke
    state = {"user_input": "hola", "intent": None, "context": ConversationContext(), "plan": [], "attempts": 0, "results": {}, "response": "", "metadata": {}}
    asyncio.run(brain._chat(state))
    s = seen["system"]
    assert "Conversacion reciente" in s and "Voseo SIEMPRE" in s
    assert "PROHIBIDO responder en ingl" in s and "1 a 3 frases" in s


def test_delay_step_before_play(brain):
    import asyncio

    from core.brain import ConversationContext, Intent, delay_seconds
    assert delay_seconds("y despues de 5 segundos pone cumbia") == 5
    assert delay_seconds("pone cumbia") == 0
    brain.plugin_registry.get_all_tools.return_value = {"music_wait": ToolSchema(name="music_wait", description="d"), "mymusic_search": ToolSchema(name="mymusic_search", description="d")} 
    state = {
        "user_input": "quita todo y despues de 5 segundos pone cumbia",
        "intent": Intent(name="music_search", confidence=0.9, entities={}),
        "context": ConversationContext(),
        "plan": [],
        "attempts": 0,
        "results": {"step_0": {"ok": True}},
        "response": "",
        "metadata": {"tool_history": ["music_control"], "sent_commands": ["m!stop"]},
    }
    out = asyncio.run(brain._plan(state))
    assert out["plan"][0].tool == "music_wait" and out["plan"][0].args["segundos"] == "5"


def test_fase2_persona_in_prompts(brain):
    import asyncio
    from unittest.mock import MagicMock

    from core.brain import PERSONA, ConversationContext
    assert "Listo." in PERSONA and "por supuesto" in PERSONA
    import pathlib
    assert "Iron Man" in pathlib.Path("persona.md").read_text(encoding="utf-8")
    seen = {}
    async def fake_ainvoke(messages):
        seen["system"] = messages[0].content
        return MagicMock(content="ok")
    brain.llm_main.ainvoke = fake_ainvoke
    state = {"user_input": "hola", "intent": None, "context": ConversationContext(), "plan": [], "attempts": 0, "results": {}, "response": "", "metadata": {}}
    asyncio.run(brain._chat(state))
    assert "Enseguida" in seen["system"] and "como IA" in seen["system"]


def test_pending_with_agrega_and_rise():
    from core.brain import _has_pending_action, deterministic_done, music_query_from_text
    assert _has_pending_action("despausala y agrega un tema de Skillet") is True
    st = {"user_input": "despausala y agrega un tema", "results": {"step_0": {"ok": True}}, "metadata": {"tool_history": ["music_control"], "sent_commands": ["m!resume"]}}
    assert deterministic_done(st) is False
    assert music_query_from_text("Monster o Rai") == "Monster o Rai"


def test_parse_compound_order():
    from core.brain import _parse_compound
    seq = _parse_compound("quita todas las musicas y pone Nublado de Paulo Londra", "music_search")
    assert [k for k, _ in seq] == ["control", "search"] and seq[0][1] == "m!stop"
    seq = _parse_compound("pausala 5 segundos y reanudala", "music_search")
    assert [(k, v) for k, v in seq] == [("control", "m!pause"), ("wait", "5"), ("control", "m!resume")]
    assert _parse_compound("pausa", "music_search") == []
    assert _parse_compound("hola como estas", "unknown") == []


def test_compound_executes_in_order(brain):
    import asyncio

    from core.brain import ConversationContext, Intent
    from plugins.base import ToolSchema
    brain.plugin_registry.get_all_tools.return_value = {n: ToolSchema(name=n, description="d") for n in ("music_control", "mymusic_search", "discord_user_send", "music_wait")}
    base = {"user_input": "quita todo y pone cumbia", "intent": Intent(name="music_search", confidence=0.9, entities={}), "context": ConversationContext(), "plan": [], "attempts": 0, "results": {}, "response": "", "metadata": {}}
    s1 = asyncio.run(brain._plan(dict(base)))
    assert s1["plan"][0].tool == "music_control"
    q = s1["metadata"]["action_queue"]
    s2state = dict(base, results={"step_0": {"ok": True}}, metadata={"tool_history": ["music_control"], "sent_commands": ["m!stop"], "action_queue": q})
    s2 = asyncio.run(brain._plan(s2state))
    assert s2["plan"][0].tool == "mymusic_search"


def test_calc_exact(brain):
    import asyncio

    from core.brain import ConversationContext, try_calc_answer
    assert try_calc_answer("Cuanto es 5 mas 5 mas 20 mas 2 menos 7?") == "25"
    assert try_calc_answer("calcula 10 por 3") == "30"
    assert try_calc_answer("cuanto es cinco mas cinco?") == "10"
    assert try_calc_answer("hola como estas") is None
    assert try_calc_answer("cuanto cuesta eso?") is None
    state = {"user_input": "cuanto es 2 mas 2?", "intent": None, "context": ConversationContext(), "plan": [], "attempts": 0, "results": {}, "response": "", "metadata": {}}
    out = asyncio.run(brain._chat(state))
    assert out["response"] == "4"


def test_scrub_identity():
    from core.brain import scrub_response
    assert scrub_response("Soy un modelo de lenguaje artificial creado para ayudar. 5 mas 5 es 10.") == "5 mas 5 es 10."
    assert scrub_response("Todo bien, señor.") == "Todo bien, señor."


def test_first_link_skips_excluded_and_blocked():
    from core.brain import _first_link
    res = {"step_0": {"ok": True, "results": [{"title": "The Resistance", "url": "https://youtu.be/AAA"}, {"title": "Monster", "url": "https://youtu.be/BBB"}]}}
    assert _first_link(res, "musica de Skillet, no la de Resistence") == "https://youtu.be/BBB"
    res2 = {"step_0": {"ok": True, "results": [{"title": "Algun dia", "url": "https://youtu.be/AAA"}]}}
    assert _first_link(res2, "pone algo") == ""
    assert _first_link(res2, "pone algun dia") == "https://youtu.be/AAA"


def test_chat_cache_and_cheap_model(brain):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from core.brain import ConversationContext
    brain.llm_fast = MagicMock()
    brain.llm_main = MagicMock()
    brain.llm_fast.ainvoke = AsyncMock(return_value=MagicMock(content="Todo bien."))
    brain.llm_main.ainvoke = AsyncMock(return_value=MagicMock(content="CARO"))
    def mk():
        return {"user_input": "todo bien?", "intent": None, "context": ConversationContext(), "plan": [], "attempts": 0, "results": {}, "response": "", "metadata": {}}
    r1 = asyncio.run(brain._chat(mk()))["response"]
    r2 = asyncio.run(brain._chat(mk()))["response"]
    assert r1 == r2 == "Todo bien."
    assert brain.llm_fast.ainvoke.call_count == 1
    assert brain.llm_main.ainvoke.call_count == 0


def test_tala_means_stop():
    from core.brain import _wants_play, control_command, fast_first_step
    assert control_command("que tala musica del discord") == "m!stop"
    s = fast_first_step("CLAUDIO, que tala musica, del discord", "discord_command")
    assert s is not None and s.tool == "music_control" and s.args["action"] == "stop"
    assert _wants_play("que tala musica del discord", ["mymusic_search"]) is False
    assert _wants_play("pone ramma", ["mymusic_search"]) is True


def test_auto_no_send_without_play_verb():
    from core.brain import Intent, auto_music_step
    state = {"user_input": "que suena lindo hoy", "intent": Intent(name="music_search", confidence=0.9, entities={}), "results": {"step_0": {"ok": True, "results": [{"title": "X", "url": "https://youtu.be/AAA"}]}}, "metadata": {"tool_history": ["mymusic_search"]}}
    assert auto_music_step(state) is None
    state["user_input"] = "que tala musica del discord"
    step = auto_music_step(state)
    assert step is not None and step.tool == "music_control" and step.args["action"] == "stop"


def test_spotify_ignored_in_music_query():
    from core.brain import music_query_from_text
    q = music_query_from_text("ponelo en Spotify, la playlist pop de mi biblioteca")
    assert "spotify" not in q.lower() and "pop" in q.lower()


def test_apodos_vault_rule():
    import pathlib
    assert "Tu Nombre" in pathlib.Path("memoria.md").read_text(encoding="utf-8")
    assert "Apodos" in pathlib.Path("ordenes.md").read_text(encoding="utf-8")


def test_route_control_goes_full(brain):
    assert brain._route_after_perceive({"user_input": "podes saltar la musica de vulta"}) == "full"
    assert brain._route_after_perceive({"user_input": "hola como estas"}) == "chat"
    assert brain._route_after_perceive({"user_input": "pausa"}) == "full"


def test_resalta_vulta_insultos():
    from core.brain import control_command, music_query_from_text
    assert control_command("resalta la musica") == "m!skip"
    assert "puta" not in music_query_from_text("resalta la musica, hijo de puta").lower()
    q = music_query_from_text("saltar la musica de vulta")
    assert "vulta" not in q.lower()


def test_excluded_words_multiword():
    from core.brain import _title_excluded, excluded_words
    ex = excluded_words("pone algo de Paulo Londra, pero no pongas Nublado, no pongas No Puedo, no pongas la de Bizarra")
    assert any("nublado" in w.lower() for w in ex), ex
    assert any("puedo" in w.lower() for w in ex), ex
    assert any("bizarra" in w.lower() for w in ex), ex
    assert not any(w.lower() == "pongas" for w in ex)
    assert _title_excluded("Nublado", ex) is True
    assert _title_excluded("Noche de Novela", ex) is False


def test_local_confirm_all_excluded():
    from core.brain import _local_confirm
    res = {"step_0": {"ok": True, "results": [{"title": "Nublado", "url": "https://youtu.be/AAA"}]}}
    out = _local_confirm(res, ["mymusic_search"], [], "no pongas Nublado")
    assert "evitar" in out and "segundo plano" not in out


def test_know_and_antes_reorder():
    from core.brain import _KNOW_RE, _parse_compound
    assert _KNOW_RE.search("el tema mas reproducido de paulo londra")
    assert _KNOW_RE.search("lo mas famoso")
    seq = _parse_compound("busca el tema mas reproducido de paulo londra y reprucilo, pero antes limpia", "music_search")
    kinds = [k for k, _ in seq]
    assert kinds[0] == "control"
    assert "resolve" in kinds and "search" in kinds
    assert kinds.index("resolve") < kinds.index("search")


def test_ask_resolve_and_queue_flow(brain):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from core.brain import ConversationContext, Intent
    from plugins.base import ToolSchema
    brain.llm_fast.ainvoke = AsyncMock(return_value=MagicMock(content="Paulo Londra - Tal Vez"))
    ans = asyncio.run(brain._ask_resolve("tema mas reproducido de paulo londra"))
    assert ans == "Paulo Londra - Tal Vez"
    brain.plugin_registry.get_all_tools.return_value = {"mymusic_search": ToolSchema(name="mymusic_search", description="d"), "discord_user_send": ToolSchema(name="discord_user_send", description="d")} 
    state = {"user_input": "busca el mas reproducido y mandalo", "intent": Intent(name="music_search", confidence=0.9, entities={}), "context": ConversationContext(), "plan": [], "attempts": 0, "results": {}, "response": "", "metadata": {"action_queue": [("resolve", None), ("search", None)]}}
    out = asyncio.run(brain._plan(state))
    assert out["plan"][0].tool == "mymusic_search"
    assert out["plan"][0].args["query"] == "Paulo Londra - Tal Vez"


def test_strip_false_start():
    from core.brain import normalize_user_text
    assert normalize_user_text("para, no, abri Chrome") == "abri Chrome"
    assert normalize_user_text("eh, no, pone cumbia") == "pone cumbia"
    assert normalize_user_text("no pongas Nublado") == "no pongas Nublado"
    assert normalize_user_text("hola como estas") == "hola como estas"


def test_cancel_step():
    from core.brain import _cancel_step
    tools = {"job_cancel": object()}
    s = _cancel_step("no, cancela eso ya", tools)
    assert s is not None and s.tool == "job_cancel"
    assert _cancel_step("olvidalo por ahora", tools).tool == "job_cancel"
    assert _cancel_step("pone cumbia", tools) is None
    assert _cancel_step("no, cancela eso", {}) is None


def test_pending_question_roundtrip(monkeypatch):
    import core.brain as bmod
    store = {}
    class FakeStore:
        def __init__(self, *a, **k):
            pass
        def remember(self, k, v, source=""): store[k] = v
        def recall(self, k): return store.get(k)
    monkeypatch.setattr("core.store.Store", FakeStore)
    assert bmod._load_pending_question() is None
    bmod._save_pending_question("Que tema queres?")
    assert bmod._load_pending_question() == "Que tema queres?"
    assert bmod.wants_reset("x") is False
