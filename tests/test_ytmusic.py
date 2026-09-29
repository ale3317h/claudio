import asyncio

from plugins.ytmusic import Plugin as YtMusicPlugin


def test_ytmusic_schema():
    p = YtMusicPlugin()
    names = {t.name for t in p.get_tools()}
    assert {"mymusic_playlists", "mymusic_history", "mymusic_search",
            "mymusic_playlist_tracks", "mymusic_liked",
            "mymusic_recent", "mymusic_forgotten",
            "search_playlists"} <= names
    assert any(i.name == "mymusic" for i in p.get_intents())


def test_parse_playlist_id():
    from plugins.ytmusic import _parse_playlist_id
    assert _parse_playlist_id(
        "https://music.youtube.com/playlist?list=PLC9x0298xN0rlZZEI8hyQqOclhRjb9aFP&si=abc"
    ) == "PLC9x0298xN0rlZZEI8hyQqOclhRjb9aFP"
    assert _parse_playlist_id("PLC9x0298xN0rlZZEI8hyQqOclhRjb9aFP") == "PLC9x0298xN0rlZZEI8hyQqOclhRjb9aFP"
    assert _parse_playlist_id("") == ""


def test_mymusic_search_normaliza_rama(monkeypatch):
    import asyncio

    import plugins.ytmusic as mod
    seen = {}

    class FakeApi:
        def search(self, query, filter="songs", limit=5):
            seen["q"] = query
            return [{"title": "en una", "videoId": "AAA",
                     "artists": [{"name": "RAMMA"}]}]

    monkeypatch.setattr(mod, "_api_all", lambda: [FakeApi()])  # noqa: PLW0108
    p = YtMusicPlugin()
    res = asyncio.run(p.execute_tool("mymusic_search", {"query": "rama en una"}))
    assert res.get("ok") is True
    assert "RAMMA" in seen["q"] and "rama" not in seen["q"].lower().replace("ramma", "")


def test_mymusic_liked_cookie_vencida(tmp_path, monkeypatch):
    import asyncio

    import core.mymusic_cache as mc
    import plugins.ytmusic as mod
    monkeypatch.setattr(mc, "cache_path", lambda vault=None: tmp_path / "vacio.json")

    class FakeApi:
        def get_liked_songs(self, limit=20):
            raise KeyError("Unable to find 'twoColumnBrowseResultsRenderer' using path ['contents'")

    monkeypatch.setattr(mod, "_api_all", lambda: [FakeApi()])  # noqa: PLW0108
    p = YtMusicPlugin()
    res = asyncio.run(p.execute_tool("mymusic_liked", {}))
    assert "error" in res and ("sesi" in res["error"].lower() or "cookie" in res["error"].lower())


def test_ytmusic_no_auth(tmp_path, monkeypatch):
    import core.mymusic_cache as mc
    monkeypatch.setattr(mc, "cache_path", lambda vault=None: tmp_path / "vacio.json")
    monkeypatch.setenv("YTMUSIC_HEADERS", str(tmp_path / "nope.json"))
    monkeypatch.setenv("YTMUSIC_OAUTH", str(tmp_path / "nope_oauth.json"))
    p = YtMusicPlugin()
    res = asyncio.run(p.execute_tool("mymusic_playlists", {}))
    assert "error" in res and "oauth" in res["error"].lower()


def test_search_playlists_mapea_afuera(monkeypatch):
    import asyncio

    import plugins.ytmusic as mod
    seen = {}

    class FakeApi:
        def search(self, query, filter="songs", limit=5):
            seen["q"] = query
            seen["filter"] = filter
            return [{"title": "Milo J Mix", "playlistId": "PLAAA",
                     "author": "YouTube Music"}]

    monkeypatch.setattr(mod, "_api_all", lambda: [FakeApi()])  # noqa: PLW0108
    p = YtMusicPlugin()
    res = asyncio.run(p.execute_tool("search_playlists", {"query": "Milo J"}))
    assert res.get("ok") is True
    assert seen["filter"] == "playlists"
    assert res["results"][0]["url"] == "https://music.youtube.com/playlist?list=PLAAA"
    assert "no tu biblioteca" in res["source"]


def test_oauth_prioritario_sobre_cookie(tmp_path, monkeypatch):
    import json

    import plugins.ytmusic as mod
    oauth = tmp_path / "oauth.json"
    oauth.write_text(json.dumps({"scope": "x", "token_type": "Bearer",
                                 "access_token": "AT", "refresh_token": "RT"}),
                     encoding="utf-8")
    monkeypatch.setenv("YTMUSIC_OAUTH", str(oauth))
    seen = {}

    class FakeYTMusic:
        def __init__(self, auth=None, oauth_credentials=None):
            seen["auth"] = auth

    import ytmusicapi
    monkeypatch.setattr(ytmusicapi, "YTMusic", FakeYTMusic)
    # YTMusic(auth=...) devuelve instancia; simulamos _api directo
    mod._api()
    assert seen.get("auth") == str(oauth)


def test_try_each_fallback_auth(monkeypatch):
    import plugins.ytmusic as mod

    class BadApi:
        def search(self, *a, **k):
            raise RuntimeError("Server returned HTTP 400: Bad Request. Request contains an invalid argument.")

    class GoodApi:
        def search(self, *a, **k):
            return [{"title": "t", "videoId": "AAA", "artists": []}]

    monkeypatch.setattr(mod, "_api_all", lambda: [BadApi(), GoodApi()])
    ok, res = mod._try_each("search", "x", filter="songs", limit=5)
    assert ok is True and res[0]["videoId"] == "AAA"


def test_try_each_no_reintenta_error_funcional(monkeypatch):
    import plugins.ytmusic as mod
    calls = []

    class Api1:
        def get_playlist(self, *a, **k):
            calls.append(1)
            raise RuntimeError("playlist inexistente")

    class Api2:
        def get_playlist(self, *a, **k):
            calls.append(2)
            return {}

    monkeypatch.setattr(mod, "_api_all", lambda: [Api1(), Api2()])
    ok, _ = mod._try_each("get_playlist", "PLxxx")
    assert ok is False and calls == [1]


def test_is_oauth_file(tmp_path):
    import json

    from plugins.ytmusic import _is_oauth_file
    ok = tmp_path / "ok.json"
    ok.write_text(json.dumps({"access_token": "a", "refresh_token": "r"}), encoding="utf-8")
    assert _is_oauth_file(str(ok)) is True
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"cookie": "YSC=..."}), encoding="utf-8")
    assert _is_oauth_file(str(bad)) is False
    assert _is_oauth_file(str(tmp_path / "nope.json")) is False


def test_recent_vacio_y_con_eventos(tmp_path):
    import asyncio
    import json

    from core.store import Store
    p = YtMusicPlugin()
    p.store = Store(tmp_path)
    assert "error" in asyncio.run(p.execute_tool("mymusic_recent", {}))
    p.store.log_event("musica", json.dumps({"title": "T", "artists": "RAMMA",
                                            "url": "https://youtu.be/AAA"}))
    res = asyncio.run(p.execute_tool("mymusic_recent", {}))
    assert res.get("ok") is True and res["tracks"][0]["url"] == "https://youtu.be/AAA"


def test_forgotten_evita_repetidos(tmp_path, monkeypatch):
    import asyncio
    import json as _json

    from core.store import Store
    p = YtMusicPlugin()
    p.store = Store(tmp_path)
    p.store.log_event("musica", _json.dumps({"title": "Viejo", "url": "https://youtu.be/OLD"}))
    monkeypatch.setattr("core.mymusic_cache.load_cache",
                        lambda *a, **k: {"liked": [
                            {"title": "Viejo", "url": "https://youtu.be/OLD"},
                            {"title": "Olvidado", "url": "https://youtu.be/NEW"}]})
    res = asyncio.run(p.execute_tool("mymusic_forgotten", {}))
    assert res.get("ok") is True
    assert res["tracks"][0]["url"] == "https://youtu.be/NEW"


def test_clean_album_query_compuesta():
    import plugins.ytmusic as mod
    assert mod._clean_album_query(
        "buscá el álbum RB de RAMMA, primero quitá este, limpiá el Discord y después poné el álbum SRAMMA") == "RV RAMMA"
    assert mod._clean_album_query("poner algun album de Poblo de Londra, un album") == "Paulo Londra"
    assert mod._strip_compound_scaffold("primero para esto y despues pone cumbia") == "pone cumbia"


def test_mymusic_album_devuelve_playlist(monkeypatch):
    import asyncio

    import plugins.ytmusic as mod
    seen = {}

    class FakeApi:
        def search(self, query, filter="songs", limit=5):
            seen["filter"] = filter
            return [{"title": "Homerun", "type": "Album", "browseId": "MPREx",
                     "artists": [{"name": "Paulo Londra"}]}]

        def get_album(self, browseId):
            seen["bid"] = browseId
            return {"title": "Homerun", "artists": [{"name": "Paulo Londra"}],
                    "tracks": [{"title": "t"}],
                    "audioPlaylistId": "OLAKx"}

    monkeypatch.setattr(mod, "_api_all", lambda: [FakeApi()])  # noqa: PLW0108
    p = YtMusicPlugin()
    res = asyncio.run(p.execute_tool("mymusic_album", {"query": "poner algun album de Poblo de Londra"}))
    assert res.get("ok") is True
    assert seen["filter"] == "albums" and seen["bid"] == "MPREx"
    assert res["url"] == "https://music.youtube.com/playlist?list=OLAKx"
    assert res["artists"] == "Paulo Londra"


def test_playlists_y_likeds_cache_primero(tmp_path, monkeypatch):
    import asyncio
    import json

    (tmp_path / "d").mkdir(exist_ok=True)
    (tmp_path / "d" / "mymusic_cache.json").write_text(json.dumps({
        "playlists": [{"title": "pop", "url": "https://music.youtube.com/playlist?list=PLPOP"}],
        "liked": [{"title": "T", "artists": "RAMMA", "url": "https://youtu.be/AAA"}]}), encoding="utf-8")

    import core.mymusic_cache as mc
    monkeypatch.setattr(mc, "cache_path", lambda vault=None: tmp_path / "d" / "mymusic_cache.json")
    p = YtMusicPlugin()
    r1 = asyncio.run(p.execute_tool("mymusic_playlists", {"limit": "25"}))
    assert r1.get("ok") is True and r1["playlists"][0]["title"] == "pop"
    assert "memoria" in r1.get("source", "")
    r2 = asyncio.run(p.execute_tool("mymusic_liked", {"limit": "20"}))
    assert r2.get("ok") is True and r2["tracks"][0]["url"] == "https://youtu.be/AAA"
