import asyncio
from unittest.mock import AsyncMock, MagicMock

from core.llm import ResilientChatModel


def _mock_model(reply=None, error=None):
    m = MagicMock()
    if error:
        m.ainvoke = AsyncMock(side_effect=Exception(error))
    else:
        m.ainvoke = AsyncMock(return_value=reply)
    return m


def test_fallback_on_primary_error():
    model = ResilientChatModel([_mock_model(error="429 rate limit"), _mock_model(reply="ok-local")], label="test")
    assert asyncio.run(model.ainvoke("hola")) == "ok-local"


def test_primary_used_when_healthy():
    fb = _mock_model(reply="ok-local")
    model = ResilientChatModel([_mock_model(reply="ok-remote"), fb], label="test")
    assert asyncio.run(model.ainvoke("hola")) == "ok-remote"
    assert fb.ainvoke.call_count == 0


def test_chain_second_fallback():
    model = ResilientChatModel([
        _mock_model(error="429"),
        _mock_model(error="500"),
        _mock_model(reply="ok-tercero"),
    ], label="test")
    assert asyncio.run(model.ainvoke("hola")) == "ok-tercero"


def test_skips_limited_backend(tmp_path):
    import time

    from core.store import Store
    store = Store(tmp_path)
    store.set_backend_state("groq", "limited", time.time() + 3600)
    bad = _mock_model(error="boom")
    good = _mock_model(reply="ok")
    model = ResilientChatModel([("groq", bad), ("ollama", good)], store=store)
    assert asyncio.run(model.ainvoke("hola")) == "ok"
    assert bad.ainvoke.call_count == 0


def test_429_tpd_marks_until_midnight(tmp_path):
    import time

    from core.store import Store
    store = Store(tmp_path)
    bad = _mock_model(error="Error code: 429 - {...} on tokens per day (TPD): Limit 200000")
    good = _mock_model(reply="ok")
    model = ResilientChatModel([("groq", bad), ("ollama", good)], store=store)
    assert asyncio.run(model.ainvoke("hola")) == "ok"
    st = store.get_backend_state("groq")
    assert st["status"] == "limited" and st["until"] > time.time()


def test_404_limits_not_disables(tmp_path):
    import time

    from core.store import Store
    store = Store(tmp_path)
    bad = _mock_model(error="404 NOT_FOUND model gone")
    good = _mock_model(reply="ok")
    model = ResilientChatModel([("gemini", bad), ("ollama", good)], store=store)
    assert asyncio.run(model.ainvoke("hola")) == "ok"
    st = store.get_backend_state("gemini")
    assert st["status"] == "limited" and st["until"] > time.time()


def test_401_still_disables(tmp_path):
    from core.store import Store
    store = Store(tmp_path)
    bad = _mock_model(error="401 Unauthorized bad key")
    good = _mock_model(reply="ok")
    model = ResilientChatModel([("groq", bad), ("ollama", good)], store=store)
    assert asyncio.run(model.ainvoke("hola")) == "ok"
    assert store.get_backend_state("groq")["status"] == "disabled"


def test_normalize_strips_think_blocks():
    from types import SimpleNamespace

    from core.llm import ResilientChatModel
    resp = SimpleNamespace(content="<think>Okay, let's see. The user says hola.</think>Todo bien, señor.")
    out = ResilientChatModel._normalize(resp)
    assert out.content == "Todo bien, señor."
    assert "Okay" not in out.content


def test_no_fallback_reraises():
    model = ResilientChatModel([_mock_model(error="boom")], label="test")
    failed = False
    try:
        asyncio.run(model.ainvoke("hola"))
    except Exception as e:
        failed = "boom" in str(e)
    assert failed


def test_astream_fallback():
    async def _go():
        primary = MagicMock()

        async def boom(messages):
            raise RuntimeError("429")
            yield

        primary.astream = boom
        fallback = MagicMock()
        fallback.ainvoke = AsyncMock(return_value=MagicMock(content="hola local"))
        model = ResilientChatModel([primary, fallback], label="test")
        out = []
        async for ch in model.astream("hola"):
            out.append(ch)
        return out

    chunks = asyncio.run(_go())
    assert len(chunks) == 1 and chunks[0].content == "hola local"


def test_build_skips_missing_keys(monkeypatch):
    import os
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    os.environ.pop("GOOGLE_API_KEY", None)
    from core.llm import build_chat_model
    model = build_chat_model("fast")
    assert isinstance(model, ResilientChatModel)
    assert len(model.chain) >= 1


def test_openrouter_needs_key(monkeypatch):
    import os
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    os.environ.pop("OPENROUTER_API_KEY", None)
    from core import llm as llm_mod
    try:
        llm_mod._build_openrouter("fast")
        raise AssertionError("deberia haber fallado")
    except RuntimeError as e:
        assert "OPENROUTER" in str(e)


def test_dream_github_need_keys(monkeypatch):
    import os
    monkeypatch.delenv("DREAMPROMPTING_API_KEY", raising=False)
    monkeypatch.delenv("GITHUB_PAT", raising=False)
    os.environ.pop("DREAMPROMPTING_API_KEY", None)
    os.environ.pop("GITHUB_PAT", None)
    from core import llm as llm_mod
    for builder, word in ((llm_mod._build_dream, "DREAM"), (llm_mod._build_github, "GITHUB")):
        try:
            builder("fast")
            raise AssertionError("deberia haber fallado")
        except RuntimeError as e:
            assert word in str(e)


def test_groq_multi_key(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEYS", "gsk_aaa,gsk_bbb")
    from core.llm import build_chat_model
    model = build_chat_model("fast")
    names = [n for n, _ in model.chain]
    assert "groq" in names and "groq2" in names


def test_usage_tracking(tmp_path):
    import asyncio

    from core.store import Store
    store = Store(tmp_path)
    assert store.llm_usage_today() == {}
    model = ResilientChatModel([("test", _mock_model(reply="hola mundo"))], store=store)
    assert asyncio.run(model.ainvoke("hola")) == "hola mundo"
    usage = store.llm_usage_today()
    assert usage["test"]["calls"] == 1
    assert usage["test"]["tokens"] > 0
    bad = ResilientChatModel([("malo", _mock_model(error="boom"))], store=store)
    try:
        asyncio.run(bad.ainvoke("hola"))
    except Exception:
        pass
    assert store.llm_usage_today()["malo"]["errors"] == 1


def test_timeout_skips_backend(tmp_path):
    import asyncio

    from core.store import Store

    async def slow(*a, **k):
        await asyncio.sleep(30)

    bad = MagicMock()
    bad.ainvoke = slow
    good = _mock_model(reply="ok")
    model = ResilientChatModel([("groq", bad), ("ollama", good)], store=Store(tmp_path))
    model.timeouts = {"groq": 0.05, "ollama": 300.0}
    assert asyncio.run(model.ainvoke("hola")) == "ok"


def test_normalize_gemini_blocks():
    from types import SimpleNamespace

    from core.llm import ResilientChatModel
    resp = SimpleNamespace(content=[
        {"type": "text", "text": "Hola Señor."},
        {"extras": {"signature": "Er4RCrsSECRETO"}},
        "  ",
    ])
    out = ResilientChatModel._normalize(resp)
    assert out.content == "Hola Señor."
    assert "Er4RCrs" not in out.content


def test_normalize_plain_string():
    from types import SimpleNamespace

    from core.llm import ResilientChatModel
    resp = SimpleNamespace(content="ya es texto")
    assert ResilientChatModel._normalize(resp).content == "ya es texto"


def test_reorder_by_usage_least_used_first(tmp_path):
    from core.llm import reorder_by_usage
    from core.store import Store
    store = Store(tmp_path)
    store.log_llm_call("groq", 4000, 4000, True)
    a, b = MagicMock(), MagicMock()
    out = reorder_by_usage([("groq", a), ("groq2", b)], store)
    assert [n for n, _ in out] == ["groq2", "groq"]


def test_reorder_errors_last(tmp_path):
    import time

    from core.llm import reorder_by_usage
    from core.store import Store
    store = Store(tmp_path)
    store.set_backend_state("groq", "limited", time.time() + 3600)
    store.log_llm_call("groq", 100, 100, False)
    a, b = MagicMock(), MagicMock()
    out = reorder_by_usage([("groq", a), ("groq2", b)], store)
    assert [n for n, _ in out][0] == "groq2"


def test_warmup_picks_first_healthy():
    import asyncio
    from unittest.mock import AsyncMock

    from core.llm import ResilientChatModel
    bad = MagicMock()
    bad.ainvoke = AsyncMock(side_effect=Exception("429"))
    good = MagicMock()
    good.ainvoke = AsyncMock(return_value=MagicMock(content="OK"))
    m = ResilientChatModel([("groq", bad), ("groq2", good)])
    assert asyncio.run(m.warmup(per_backend_timeout=5)) == "groq2"


def test_last_backend_tracked():
    import asyncio

    from core.llm import ResilientChatModel
    good = MagicMock()
    good.ainvoke = AsyncMock(return_value=MagicMock(content="OK"))
    m = ResilientChatModel([("groq", good)])
    assert m.last_backend is None
    asyncio.run(m.ainvoke("hola"))
    assert m.last_backend == "groq"


def test_warmup_collects_errors():
    import asyncio
    from unittest.mock import AsyncMock

    from core.llm import ResilientChatModel
    bad = MagicMock()
    bad.ainvoke = AsyncMock(side_effect=Exception("401 Unauthorized"))
    good = MagicMock()
    good.ainvoke = AsyncMock(return_value=MagicMock(content="OK"))
    m = ResilientChatModel([("groq", bad), ("groq2", good)])
    assert asyncio.run(m.warmup(per_backend_timeout=5)) == "groq2"
    assert m.warmup_errors and m.warmup_errors[0][0] == "groq"


def test_astream_preserves_spaces():
    import asyncio
    from types import SimpleNamespace

    from core.llm import ResilientChatModel
    async def _go():
        m = MagicMock()
        async def gen(messages, config=None):
            for t in ["Hola", " señor", "."]:
                yield SimpleNamespace(content=t)
        m.astream = gen
        model = ResilientChatModel([("ollama", m)])
        return "".join([(c.content or "") async for c in model.astream("hola")])
    assert asyncio.run(_go()) == "Hola señor."


def test_healthcheck_checks_all():
    import asyncio
    from unittest.mock import AsyncMock

    from core.llm import ResilientChatModel
    ok = MagicMock()
    ok.ainvoke = AsyncMock(return_value=MagicMock(content="OK"))
    bad = MagicMock()
    bad.ainvoke = AsyncMock(side_effect=Exception("429"))
    m = ResilientChatModel([("groq", ok), ("gemini", bad)])
    res = asyncio.run(m.healthcheck(per_backend_timeout=5))
    assert res[0][:2] == ("groq", True) and isinstance(res[0][2], int)
    assert res[1][:2] == ("gemini", False) and res[1][3]
    assert m.warmup_errors and m.warmup_errors[0][0] == "gemini"


def test_warmup_uses_long_ollama_timeout():
    import asyncio

    from core.llm import ResilientChatModel
    async def slow_probe(messages):
        await asyncio.sleep(0.2)
        return MagicMock(content="OK")
    m = MagicMock()
    m.ainvoke = slow_probe
    model = ResilientChatModel([("ollama", m)])
    model.timeouts = {"ollama": 5.0}
    assert asyncio.run(model.warmup(per_backend_timeout=0.05)) == "ollama"


def test_hedge_hang_resolves_fast():
    import asyncio
    import time
    from unittest.mock import AsyncMock, MagicMock

    from core.llm import ResilientChatModel
    async def hang(messages, config=None):
        await asyncio.sleep(30)
        return MagicMock(content="tarde")
    slow = MagicMock()
    slow.ainvoke = hang
    fast = MagicMock()
    fast.ainvoke = AsyncMock(return_value=MagicMock(content="rapido"))
    m = ResilientChatModel([("lento", slow), ("rapido", fast)], hedge_after_s=0.3)
    t0 = time.monotonic()
    out = asyncio.run(m.ainvoke("hola"))
    dt = time.monotonic() - t0
    assert out.content == "rapido"
    assert dt < 6.0, dt


def test_hedge_fast_error_no_hedge_wait():
    import asyncio
    import time
    from unittest.mock import AsyncMock, MagicMock

    from core.llm import ResilientChatModel
    bad = MagicMock()
    bad.ainvoke = AsyncMock(side_effect=Exception("429"))
    good = MagicMock()
    good.ainvoke = AsyncMock(return_value=MagicMock(content="ok2"))
    m = ResilientChatModel([("a", bad), ("b", good)], hedge_after_s=30)
    t0 = time.monotonic()
    out = asyncio.run(m.ainvoke("hola"))
    assert out.content == "ok2" and time.monotonic() - t0 < 5.0


def test_ranked_prefers_low_ema():
    from unittest.mock import MagicMock

    from core.llm import ResilientChatModel
    m = ResilientChatModel([("a", MagicMock()), ("b", MagicMock())])
    m._note_lat("b", 300.0)
    m._note_lat("a", 5000.0)
    assert [n for n, _ in m._ranked_candidates()] == ["b", "a"]
def test_healthcheck_reprobes_disabled_fixed_key(tmp_path):
    from core.store import Store
    store = Store(tmp_path)
    store.set_backend_state("groq2", "disabled", 0.0)
    good = _mock_model(reply="ok")
    model = ResilientChatModel([("groq2", good)], store=store)
    res = asyncio.run(model.healthcheck(per_backend_timeout=5))
    assert res[0][:2] == ("groq2", True)
    assert store.get_backend_state("groq2")["status"] == "ok"


def test_healthcheck_dead_key_stays_disabled(tmp_path):
    from core.store import Store
    store = Store(tmp_path)
    store.set_backend_state("groq2", "disabled", 0.0)
    bad = _mock_model(error="401 Unauthorized")
    model = ResilientChatModel([("groq2", bad)], store=store)
    res = asyncio.run(model.healthcheck(per_backend_timeout=5))
    assert res[0][:2] == ("groq2", False)
    assert store.get_backend_state("groq2")["status"] == "disabled"


def test_warmup_revives_disabled_fixed_key(tmp_path):
    from core.store import Store
    store = Store(tmp_path)
    store.set_backend_state("groq2", "disabled", 0.0)
    fixed = _mock_model(reply="ok")
    model = ResilientChatModel([("groq2", fixed)], store=store)
    assert asyncio.run(model.warmup(per_backend_timeout=5)) == "groq2"


def test_warmup_tries_usable_before_disabled(tmp_path):
    from core.store import Store
    store = Store(tmp_path)
    store.set_backend_state("groq", "disabled", 0.0)
    fixed = _mock_model(reply="ok-fixed")
    good = _mock_model(reply="ok")
    model = ResilientChatModel([("groq", fixed), ("groq2", good)], store=store)
    assert asyncio.run(model.warmup(per_backend_timeout=5)) == "groq2"
    assert good.ainvoke.call_count == 1
    assert fixed.ainvoke.call_count == 0
def test_build_meta_sin_key_falla(monkeypatch):
    import core.llm as mod
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.setattr(mod.os, "getenv", lambda k, d="": "")
    try:
        mod._build_meta("main")
        assert False, "deberia fallar sin key"
    except RuntimeError as e:
        assert "MODEL_API_KEY" in str(e)


def test_build_meta_con_key(monkeypatch):
    import core.llm as mod
    monkeypatch.setattr(mod.os, "getenv",
                        lambda k, d="": "TESTKEY" if k == "MODEL_API_KEY" else d)
    m = mod._build_meta("main")
    assert m.model_name == "muse-spark-1.1"
    assert "api.meta.ai" in str(m.openai_api_base)
    assert "meta" in mod._BUILDERS
