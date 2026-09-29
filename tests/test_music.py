from plugins.music import Plugin as MusicPlugin
from plugins.music import _watch_url


def test_music_schema():
    p = MusicPlugin()
    assert p.manifest.name == "music"
    assert any(t.name == "music_search" for t in p.get_tools())
    assert any(i.name == "music_search" for i in p.get_intents())


def test_watch_url():
    assert _watch_url({"webpage_url": "https://www.youtube.com/watch?v=abc"}) == "https://www.youtube.com/watch?v=abc"
    assert _watch_url({"id": "dQw4w9WgXcQ"}) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert _watch_url({}) == ""


def test_music_search_empty():
    import asyncio
    p = MusicPlugin()
    res = asyncio.run(p.execute_tool("music_search", {"query": ""}))
    assert "error" in res


def test_music_play_skips_recent(tmp_path):
    import asyncio

    from core.store import Store
    p = MusicPlugin()
    p.store = Store(tmp_path)
    p._search = lambda q, n=5: [
        {"title": "ramma - primer tema", "url": "https://youtu.be/AAA"},
        {"title": "ramma - otro tema", "url": "https://youtu.be/BBB"},
        {"title": "ramma - tercer tema", "url": "https://youtu.be/CCC"},
    ]
    res1 = asyncio.run(p.execute_tool("music_play", {"query": "ramma", "artist": "Ramma"}))
    assert res1["pick"]["url"] == "https://youtu.be/AAA"
    res2 = asyncio.run(p.execute_tool("music_play", {"query": "ramma", "artist": "Ramma"}))
    assert res2["pick"]["url"] == "https://youtu.be/BBB"
    assert p.store.recall("artista_ramma") == "ramma - otro tema"


def test_music_play_skips_blocked_default():
    import asyncio

    from core.store import Store
    from plugins.music import Plugin as MusicPlugin
    p = MusicPlugin()
    import tempfile
    p.store = Store(tempfile.mkdtemp())
    p._search = lambda q, n=5: [
        {"title": "ramma - algún día", "url": "https://youtu.be/AAA"},
        {"title": "ramma - otro tema", "url": "https://youtu.be/BBB"},
    ]
    res = asyncio.run(p.execute_tool("music_play", {"query": "ramma", "artist": "Ramma"}))
    assert res["pick"]["url"] == "https://youtu.be/BBB"
    res = asyncio.run(p.execute_tool("music_play", {"query": "algún día de ramma"}))
    assert res["pick"]["url"] == "https://youtu.be/AAA"


def test_is_playable():
    from plugins.music import _is_playable
    assert _is_playable("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert _is_playable("https://youtu.be/dQw4w9WgXcQ")
    assert not _is_playable("https://www.youtube.com/channel/UCxxx")
    assert not _is_playable("")


def test_music_cache_hit(tmp_path):
    from core.store import Store
    from plugins.music import Plugin
    p = Plugin()
    p.store = Store(tmp_path)
    p._cache_put("ramma", [{"title": "T", "url": "https://youtu.be/AAA"}])
    assert p._search("ramma", 3) == [{"title": "T", "url": "https://youtu.be/AAA"}]


def test_music_cache_expired(tmp_path):
    from core.store import Store
    from plugins.music import Plugin
    p = Plugin()
    p.store = Store(tmp_path)
    p.store.remember("musiccache:ramma", '{"ts": 1, "results": []}')
    assert p._cache_get("ramma") is None


def test_music_wait_tool():
    import asyncio
    import time

    from plugins.music import Plugin
    t0 = time.time()
    res = asyncio.run(Plugin().execute_tool("music_wait", {"segundos": "1"}))
    assert res.get("ok") is True and res.get("segundos") == 1 and time.time() - t0 >= 1.0


def test_canon_rise_rai():
    from plugins.music import canon_query
    assert "Rise" in canon_query("Monster o Rai")
