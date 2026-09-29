import asyncio


def _plug():
    from plugins.web_search import Plugin
    p = Plugin()
    asyncio.run(_noop_init(p))
    return p


async def _noop_init(p):
    await p.initialize(None)


def test_web_search_schema():
    p = _plug()
    assert p.manifest.name == "web_search"
    assert any(t.name == "web_search" for t in p.get_tools())
    assert any(i.name == "web_search" for i in p.get_intents())


def test_web_search_sin_key_usa_duck_gratis(monkeypatch):
    import plugins.web_search as mod

    html = ('<a rel="nofollow" class="result__a" '
            'href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fx.com%2F1&rut=aa">Titulo <b>Uno</b></a>'
            '<a class="result__snippet" href="https://x.com/1">Snip uno largo</a>'
            '<a rel="nofollow" class="result__a" '
            'href="https://y.com/2">Titulo Dos</a>')

    class FakeResp:
        status_code = 200
        text = html

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            assert params and len(params["q"]) >= 3
            return FakeResp()

    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setattr(mod.os, "getenv", lambda k, d="": "")
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    p = _plug()
    res = asyncio.run(p.execute_tool("web_search", {"query": "hola mundo"}))
    assert res.get("ok") is True and res.get("source") == "duckduckgo"
    assert res["results"][0]["url"] == "https://x.com/1"
    assert res["results"][0]["title"] == "Titulo Uno"
    assert res["results"][1]["url"] == "https://y.com/2"


def test_web_search_duck_bloqueado_da_error(monkeypatch):
    import plugins.web_search as mod

    class FakeResp:
        status_code = 202
        text = "anomaly-modal"

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            return FakeResp()

    monkeypatch.setattr(mod.os, "getenv", lambda k, d="": "")
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    p = _plug()
    res = asyncio.run(p.execute_tool("web_search", {"query": "hola mundo"}))
    assert "error" in res


def test_web_search_limpia_y_mapea(monkeypatch):
    import plugins.web_search as mod

    seen = {}

    class FakeResp:
        status_code = 200

        def json(self):
            return {"answer": "A" * 700,
                    "results": [{"title": "T", "url": "https://x", "content": "C" * 500}]}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            seen["q"] = json["query"]
            seen["depth"] = json["search_depth"]
            return FakeResp()

    monkeypatch.setattr(mod.os, "getenv", lambda k, d="": "KEY")
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    p = _plug()
    res = asyncio.run(p.execute_tool("web_search", {"query": "buscá en google resultado Boca River por favor"}))
    assert res.get("ok") is True
    assert seen["q"] == "resultado boca river"  # scaffold lowercased, Tavily igual entiende
    assert seen["depth"] == "advanced"
    assert len(res["answer"]) <= 600 and len(res["results"][0]["snippet"]) <= 400


def test_web_no_traga_musica():
    from core.brain import fast_path_intent
    assert fast_path_intent("buscá en google quién ganó").name == "web_search"
    assert fast_path_intent("poné RAMMA en discord").name == "music_search"
def test_web_search_no_filtra_historia(monkeypatch):
    import plugins.web_search as mod

    seen = []

    class FakeResp:
        status_code = 200

        def json(self):
            return {"results": []}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            seen.append(dict(json))
            return FakeResp()

    monkeypatch.setattr(mod.os, "getenv", lambda k, d="": "KEY")
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    p = _plug()
    asyncio.run(p.execute_tool("web_search", {"query": "final libertadores 2018",
                                              "time_range": "week"}))
    assert "time_range" not in seen[0]  # 2018: el filtro mataria historia
    asyncio.run(p.execute_tool("web_search", {"query": "resultado Boca hoy",
                                              "time_range": "week"}))
    assert seen[1].get("time_range") == "week"


def test_web_search_pasa_time_range(monkeypatch):
    import plugins.web_search as mod

    seen = []

    class FakeResp:
        status_code = 200

        def json(self):
            return {"results": []}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            seen.append(dict(json))
            return FakeResp()

    monkeypatch.setattr(mod.os, "getenv", lambda k, d="": "KEY")
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    p = _plug()
    asyncio.run(p.execute_tool("web_search", {"query": "resultado Boca",
                                              "time_range": "week"}))
    assert seen[0].get("time_range") == "week"
    asyncio.run(p.execute_tool("web_search", {"query": "resultado Boca",
                                              "time_range": "cuatrimestre"}))
    assert "time_range" not in seen[1]


def test_web_search_rota_keys(monkeypatch):
    import plugins.web_search as mod

    calls = []

    class FakeResp:
        def __init__(self, code):
            self.status_code = code
            self.text = "x"

        def json(self):
            return {"results": [{"title": "T", "url": "https://x",
                                 "content": "C"}]}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            calls.append(json["api_key"])
            if len(calls) == 1:
                return FakeResp(401)
            return FakeResp(200)

    monkeypatch.setattr(mod.os, "getenv",
                        lambda k, d="": "KEY1,KEY2" if k == "TAVILY_API_KEYS"
                        else ("PRIMARY" if k == "TAVILY_API_KEY" else d))
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    p = _plug()
    res = asyncio.run(p.execute_tool("web_search", {"query": "hola mundo"}))
    assert res.get("ok") is True and res.get("source") == "tavily"
    assert calls == ["PRIMARY", "KEY1"], calls  # muerta -> siguiente


def test_web_search_todas_muertas_da_error(monkeypatch):
    import plugins.web_search as mod

    class FakeResp:
        status_code = 429
        text = "quota"

        def json(self):
            return {}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            return FakeResp()

    monkeypatch.setattr(mod.os, "getenv",
                        lambda k, d="": "K1" if k == "TAVILY_API_KEY" else d)
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    p = _plug()
    res = asyncio.run(p.execute_tool("web_search", {"query": "hola mundo"}))
    assert "error" in res and "uota" in res["error"]
