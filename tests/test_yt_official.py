import json


def test_official_sin_oauth(tmp_path, monkeypatch):
    monkeypatch.setenv("YTMUSIC_OAUTH", str(tmp_path / "nope.json"))
    monkeypatch.delenv("YTMUSIC_CLIENT_ID", raising=False)
    monkeypatch.delenv("YTMUSIC_CLIENT_SECRET", raising=False)
    from core import yt_official as yo
    assert yo.load_credentials(vault=tmp_path) is None
    res = yo.sync_official(vault=tmp_path)
    assert res.get("ok") is False


def test_official_merge_cache(tmp_path):
    import core.mymusic_cache as mc
    # cache con playlist oficial Ponk
    data = {
        "playlists": [{"title": "Ponk", "url": "https://music.youtube.com/playlist?list=PLX"}],
        "liked": [],
        "tranqui_tracks": [],
        "official_tracks": {
            "PLX": {"title": "Ponk", "items": [
                {"title": "T1", "artists": "RAMMA", "url": "https://www.youtube.com/watch?v=AAA"},
                {"title": "T2", "artists": "Otro", "url": "https://www.youtube.com/watch?v=BBB"},
            ]},
        },
    }
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "mymusic_cache.json").write_text(json.dumps(data), encoding="utf-8")
    assert mc.find_playlist("ponk", vault=tmp_path)["url"].endswith("PLX")
    pick = mc.pick_cached(source="ponk", vault=tmp_path)
    assert pick and pick["url"].startswith("https://www.youtube.com/watch")
    pick2 = mc.pick_cached(artist="RAMMA", vault=tmp_path)
    assert pick2 and "RAMMA" in pick2.get("artists", "")
