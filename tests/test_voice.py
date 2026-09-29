import numpy as np

from core.voice import clean_for_speech
from plugins.stt_whisper import Plugin as SttPlugin
from plugins.tts_edge import Plugin as TtsPlugin


def test_clean_for_speech():
    assert clean_for_speech("**Hola**, ¿qué tal?") == "Hola, ¿qué tal?"
    assert "```" not in clean_for_speech("```python\nx=1\n``` listo")
    assert clean_for_speech("[CLAUDIO](http://x)") == "CLAUDIO"
    assert clean_for_speech("") == ""


def test_stt_schema():
    p = SttPlugin()
    assert p.manifest.name == "stt_whisper"
    assert any(t.name == "transcribe" for t in p.get_tools())
    assert p.get_intents() == []


def test_tts_schema():
    p = TtsPlugin()
    assert p.manifest.name == "tts_edge"
    assert any(t.name == "speak" for t in p.get_tools())
    assert p.get_intents() == []


def test_wav_bytes_roundtrip():
    import io

    import soundfile as sf

    from plugins.stt_whisper import _to_wav_bytes
    tone = (np.sin(2 * np.pi * 440 * np.arange(16000) / 16000) * 10000).astype(np.int16)
    data, sr = sf.read(io.BytesIO(_to_wav_bytes(tone)), dtype="int16", always_2d=False)
    assert sr == 16000
    assert len(data) == 16000


def test_voice_defaults():
    from core.config import get_settings
    v = get_settings().voice
    assert v.stt_backend == "groq"
    assert "Jorge" in v.tts_voice  # CLAUDIO latino estilo Iron Man (decision de Señor)
    assert v.tts_engine == "edge"
    assert hasattr(v, "tts_pitch")
    assert v.tts_rate.startswith("+")  # agil pero calmo (decision de Señor)


def test_record_windows_generous():
    from core.config import get_settings
    from core.voice import VoiceLoop
    v = get_settings().voice
    loop = VoiceLoop(None, None, None,
                     silence_seconds=v.silence_seconds,
                     max_record_seconds=v.max_record_seconds)
    # Ventana agil para llamada: 1.6s silencio / 30s max (antes 3.0/60, se sentia lerdo)
    assert loop.silence_seconds == v.silence_seconds
    assert loop.max_record_seconds == v.max_record_seconds
    assert 1.0 <= loop.silence_seconds <= 2.5
    assert 15.0 <= loop.max_record_seconds <= 45.0


def test_clean_emoji_url():
    from core.voice import clean_for_speech
    assert clean_for_speech("mirá https://youtu.be/abc123 qué bueno") == "mirá qué bueno"
    assert clean_for_speech("Listo 🎶🎧") == "Listo"
    assert clean_for_speech("hola 😊 como estas") == "hola como estas"


def test_split_small():
    from core.voice import _split_small
    assert _split_small("Hola.") == ["Hola."]
    long_text = "Palabra. " * 60
    parts = _split_small(long_text)
    assert len(parts) > 1
    assert all(len(p) <= 180 for p in parts)
    assert " ".join(parts).split() == long_text.split()


def test_calibrate_threshold():
    import numpy as np

    from core.voice import calibrate_threshold
    silent = [np.zeros(1600, dtype=np.float32)]
    assert calibrate_threshold(silent) == 0.015
    assert calibrate_threshold([]) == 0.02
    loud = [np.ones(1600, dtype=np.float32) * 0.1]
    assert calibrate_threshold(loud) == 0.06


def test_speak_fluent_sequential():
    import asyncio

    import core.voice as V

    made, played = [], []

    class FakeTTS:
        async def execute_tool(self, name, args):
            assert name == "speak"
            assert args.get("play") is False
            made.append(args["text"])
            return {"file": f"/tmp/f{len(made)}"}

    async def fake_play(path):
        played.append(path)

    orig_play = V._play_file
    V._play_file = fake_play
    try:
        assert asyncio.run(V.speak_fluent(FakeTTS(), "Hola Señor. Todo bien?")) is True
    finally:
        V._play_file = orig_play
    assert made == ["Hola Señor. Todo bien?"]
    assert played == ["/tmp/f1"]


def test_speak_fluent_interrupt():
    import asyncio

    import core.voice as V

    made = []

    class FakeTTS:
        async def execute_tool(self, name, args):
            made.append(args["text"])
            return {"file": "/tmp/fx"}

    async def fake_play(path):
        return None

    async def mic_yes():
        return len(made) >= 1

    orig_play = V._play_file
    V._play_file = fake_play
    try:
        long_text = "Primera frase. " + "palabra " * 60 + "Segunda frase."
        assert asyncio.run(V.speak_fluent(FakeTTS(), long_text, mic_check=mic_yes)) is False
    finally:
        V._play_file = orig_play
    assert len(made) >= 1


def test_narration_for():
    from core.voice import narration_for
    assert narration_for("music_search", {"query": "soda stereo"}) == "Buscando soda stereo..."
    assert narration_for("browser_open", {"url": "https://x.com/" + "y" * 100}).endswith("...")
    assert narration_for("discord_send", {"message": "hola"}) is None
    assert narration_for("desconocida", {}) is None


def test_narrator_subscribe_emit_unsubscribe():
    import asyncio

    from core.events import event_bus
    from core.voice import ToolNarrator
    seen = []

    async def fake_speak(text):
        seen.append(text)

    async def _go():
        narrator = ToolNarrator(fake_speak)
        await narrator.start()
        await event_bus.emit("on_tool_call", "music_search", {"query": "tranqui"})
        await event_bus.emit("on_tool_call", "discord_send", {"message": "hola"})
        await narrator.stop()
        await event_bus.emit("on_tool_call", "music_search", {"query": "otra"})
        return seen

    assert asyncio.run(_go()) == ["Buscando tranqui..."]


def test_split_sentences():
    from core.voice import _split_sentences
    assert _split_sentences("Hola Señor.") == ["Hola Señor."]
    s240 = "Palabra " * 30
    chunks = _split_sentences(f"{s240}. {s240}. {s240}.")
    assert len(chunks) == 3
    assert all(len(c) <= 400 for c in chunks)
    long_text = "Palabra. " * 200
    assert all(len(c) <= 400 for c in _split_sentences(long_text))


def test_fix_transcript_spelled_claudio():
    from plugins.stt_whisper import fix_transcript
    assert fix_transcript("C.L.A.U.D.I.O. me escuchas bien") == "CLAUDIO me escuchas bien"
    assert fix_transcript("C. L. A. hola") == "CLAUDIO hola"
    assert fix_transcript("el F.B.I. vino") == "el F.B.I. vino"
    assert fix_transcript("che carvis estas?") == "CLAUDIO estas?"
    assert fix_transcript("por que puso eso? CLAUDIO, pone cumbia") == "CLAUDIO, pone cumbia"


def test_fix_transcript_soundalikes_ramma():
    from plugins.stt_whisper import fix_transcript
    assert fix_transcript("pone algo de carby") == "pone algo de RAMMA"
    assert fix_transcript("tema de carbi en discord") == "tema de RAMMA en discord"


def test_fix_transcript_disco_es_discord():
    from plugins.stt_whisper import fix_transcript
    assert fix_transcript("limpia la música del disco") == "limpia la música del Discord"
    assert fix_transcript("ponelo en el disco") == "ponelo en el Discord"


def test_hallucination_loop():
    from plugins.stt_whisper import fix_transcript, is_hallucination_loop
    lyrics = ("Eres el Tema, Algún día, Baja, Algún día, Otro beso, Nostalgia. "
              "Tema, Algún día, Otro beso, Nostalgia. Tema, Algún día, Otro beso.")
    assert is_hallucination_loop(lyrics) is True
    assert fix_transcript(lyrics) == ""
    gacete = ("La riqueza de Gacete hoy de nuevo, por lo que fue de esta mañana, "
              "por lo que fue de esta mañana. Y claro, cuando se quede la oportunidad, "
              "voy a pasar a la batalla. Y. Y. Y. Y. Y. Y. Y. Y. Gracias.")
    assert is_hallucination_loop(gacete) is True
    assert fix_transcript("Claudio poné la playlist pop en discord por favor") != ""
    assert is_hallucination_loop("Claudio poné la playlist pop en discord por favor") is False
    assert is_hallucination_loop("hola") is False


def test_transcribe_array_puerta_silencio():
    import numpy as np

    from plugins.stt_whisper import Plugin
    p = Plugin()
    p.backend = "groq"  # gate actua antes de cualquier backend
    quiet = (np.random.RandomState(0).randn(16000) * 30).astype(np.int16)
    assert p.transcribe_array(quiet)["text"] == ""


def test_gracias_suelto_es_alucinacion():
    from plugins.stt_whisper import fix_transcript
    assert fix_transcript("Gracias.") == ""
    assert fix_transcript("  gracias  ") == ""
    assert fix_transcript("gracias Claudio") == "Claudio"  # wake-strip previo
    assert fix_transcript("gracias por poner eso") == "gracias por poner eso"


def test_fix_transcript_paulo_and_ytspam():
    from plugins.stt_whisper import fix_transcript
    assert fix_transcript("un tema de poblodondra") == "un tema de Paulo Londra"
    assert fix_transcript("pablo londra en vivo") == "Paulo Londra en vivo"
    assert "video" not in fix_transcript("hola gracias por ver el video chau")
    assert "suscribete" not in fix_transcript("chau suscribete al canal").lower()


def test_denoise_gate_and_passthrough():
    import numpy as np

    from core.voice import denoise_audio
    silence = np.zeros(16000, dtype=np.float32)
    out = denoise_audio(silence)
    assert out.dtype == np.float32 and np.max(np.abs(out)) < 1e-6
    t = np.arange(16000, dtype=np.float32) / 16000.0
    sine = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    out = denoise_audio(sine)
    assert np.sqrt(np.mean(out.astype(np.float64) ** 2)) > 0.1
    assert np.array_equal(denoise_audio(sine, enabled=False), sine)
    assert denoise_audio(np.array([], dtype=np.float32)).size == 0


def test_ptt_key_probe_returns_bool():
    from core.voice import VK_ESCAPE, VK_PTT, _key_down, _resolve_ptt_key
    assert VK_PTT == 0x90 and VK_ESCAPE == 0x1B
    assert _resolve_ptt_key("pause") == (0x13, "PAUSE")
    assert _resolve_ptt_key("numlock") == (0x90, "BLOQ NUM")
    assert _resolve_ptt_key("scroll") == (0x91, "SCROLL")
    assert _resolve_ptt_key("rctrl") == (0xA3, "CTRL DER")
    assert _resolve_ptt_key("0x2D") == (0x2D, "0X2D")
    assert _resolve_ptt_key("cualquiercosa") == (VK_PTT, "BLOQ NUM")
    assert _resolve_ptt_key("") == (VK_PTT, "BLOQ NUM")
    assert isinstance(_key_down(VK_PTT), bool)
    assert isinstance(_key_down(VK_ESCAPE), bool)


def test_fix_transcript_renovar_es_reanudar():
    from plugins.stt_whisper import fix_transcript
    assert fix_transcript("renovar la musica") == "reanudar la musica"
    assert fix_transcript("renueva eso") == "reanudar eso"


def test_tts_cache_short_phrases(tmp_path, monkeypatch):
    import asyncio
    import os

    import edge_tts

    from plugins.tts_edge import Plugin
    calls = []

    class FakeComm:
        def __init__(self, *a, **k):
            calls.append(a)

        async def save(self, path):
            with open(path, "wb") as f:  # noqa: ASYNC230 - fake de test
                f.write(b"MP3" * 100)

    monkeypatch.setattr(edge_tts, "Communicate", FakeComm)
    p = Plugin()
    p.voice = "v"
    p.rate = "+10%"
    p.pitch = "-15Hz"
    monkeypatch.setattr(p, "_cache_dir", lambda: tmp_path)
    r1 = asyncio.run(p.execute_tool("speak", {"text": "Pausado.", "play": False}))
    r2 = asyncio.run(p.execute_tool("speak", {"text": "Pausado.", "play": False}))
    assert len(calls) == 1 and r2.get("cached") is True
    assert r1["file"] != r2["file"]
    os.remove(r1["file"])
    os.remove(r2["file"])


def test_stt_false_start():
    from plugins.stt_whisper import fix_transcript
    assert fix_transcript("para, no, abri Chrome") == "abri Chrome"
    assert fix_transcript("no pongas eso") == "no pongas eso"


def test_tts_no_ssml_texto_plano(monkeypatch):
    """Regression: edge-tts 7.2.8 escapa SSML como texto plano y el servicio
    LEE las etiquetas. El plugin debe mandar texto plano + rate/pitch sueltos."""
    import asyncio
    import pathlib
    import tempfile

    import edge_tts

    from plugins.tts_edge import Plugin
    seen = {}

    class FakeComm:
        def __init__(self, text, voice, **kw):
            seen["text"] = text
            seen["kw"] = kw

        async def save(self, path):
            with open(path, "wb") as f:  # noqa: ASYNC230 - fake de test
                f.write(b"MP3" * 100)

    monkeypatch.setattr(edge_tts, "Communicate", FakeComm)
    p = Plugin()
    p.voice = "es-MX-JorgeNeural"
    p.rate = "-5%"
    p.pitch = "-15Hz"
    monkeypatch.setattr(p, "_cache_dir", lambda: pathlib.Path(tempfile.mkdtemp()))
    r = asyncio.run(p.execute_tool("speak", {"text": "Hola amigo", "play": False}))
    assert r.get("ok") is True
    assert "<speak" not in seen["text"] and "xmlns" not in seen["text"]
    assert seen["text"] == "Hola amigo"
    assert seen["kw"].get("rate") == "-5%" and seen["kw"].get("pitch") == "-15Hz"
    import os as _os
    _os.remove(r["file"])
def test_stuck_update_corta_loop():
    from core.voice import _stuck_update
    stuck, p, n = _stuck_update("", 0, "Saja B")
    assert (stuck, p, n) == (False, "saja b", 1)
    stuck, p, n = _stuck_update(p, n, "saja b")
    assert (stuck, n) == (False, 2)
    stuck, p, n = _stuck_update(p, n, "Saja B")
    assert stuck is True and n == 0
    # Texto distinto resetea la racha.
    stuck, p, n = _stuck_update(p, 2, "pausa la musica")
    assert (stuck, n) == (False, 1)
    # Vacio resetea.
    assert _stuck_update(p, 2, "   ") == (False, "", 0)


def test_stop_speakers_llama_stop(monkeypatch):
    import sys
    import types

    from core import voice as vmod
    calls = []
    fake = types.ModuleType("sounddevice")
    fake.stop = lambda: calls.append("stop")
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    vmod._stop_speakers()
    assert calls == ["stop"]


def test_wait_press_corta_parlantes(monkeypatch):
    import sys
    import types

    from core import voice as vmod
    calls = []
    fake = types.ModuleType("sounddevice")
    fake.stop = lambda: calls.append("stop")
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    seq = iter([False, True, True])  # esc? no - ptt? si - debounce? si
    monkeypatch.setattr(vmod, "_key_down", lambda vk: next(seq, False))
    from types import SimpleNamespace
    assert vmod.VoiceLoop._wait_press(SimpleNamespace(vk_ptt=0x90)) == "ptt"
    assert calls == ["stop"]
def test_mic_report_marca_nivel():
    from core.voice import _mic_report
    ok = _mic_report(0.5, 2.0)
    assert "2.0s" in ok and "MIC MUY BAJO" not in ok
    bajo = _mic_report(0.001, 1.5)
    assert "MIC MUY BAJO" in bajo
    assert "MIC MUY BAJO" in _mic_report(0.0, 1.0)  # silencio: -inf dB, avisa


def test_save_last_mic_graba_wav(tmp_path):
    import wave

    import numpy as np

    from core.voice import _save_last_mic
    pcm = (np.sin(2 * np.pi * 440 * np.arange(1600) / 16000.0) * 10000).astype(np.int16)
    out = tmp_path / "last_mic.wav"
    _save_last_mic(out, pcm, 16000)
    assert out.exists() and out.stat().st_size > 44
    with wave.open(str(out), "rb") as w:
        assert w.getnchannels() == 1 and w.getframerate() == 16000


def test_groq_model_configurable():
    from core.config import settings
    assert (settings.voice.stt_groq_model or "").startswith("whisper-large-v3")
def test_is_silence_corta_ruido_digital():
    from core.voice import _is_silence
    assert _is_silence(0.0) is True
    assert _is_silence(0.00003) is True  # -90 dBFS: el caso real del usuario
    assert _is_silence(0.019) is True
    assert _is_silence(0.021) is False
    assert _is_silence(0.5) is False
def test_mic_device_name(monkeypatch):
    import sys
    import types

    from core import voice as vmod
    fake = types.ModuleType("sounddevice")
    fake.default = types.SimpleNamespace(device=(3, None))
    fake.query_devices = lambda i: {"name": "Voicemod Virtual Audio Device"}
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    assert vmod._mic_device_name() == "Voicemod Virtual Audio Device"
    fake.query_devices = lambda i: 1 / 0
    assert vmod._mic_device_name() == "desconocido"
def test_kling_image_to_data_uri(tmp_path):
    import base64
    import sys

    sys.path.insert(0, "scripts")
    from kling_studio import image_to_data_uri
    # PNG minimo de 1x1.
    raw = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
    img = tmp_path / "frame.png"
    img.write_bytes(raw)
    uri = image_to_data_uri(str(img))
    assert uri.startswith("data:image/png;base64,")
    txt = tmp_path / "nota.txt"
    txt.write_text("hola")
    try:
        image_to_data_uri(str(txt))
        assert False, "deberia fallar con .txt"
    except ValueError:
        pass
