from core.call import EXIT_WORDS, TurnDetector


def test_turn_starts_after_sustained_speech():
    d = TurnDetector(threshold=0.02, start_speech=0.4, end_silence=1.0)
    assert d.feed(0.0, 0.1) == "idle"
    assert d.feed(0.05, 0.1) == "idle"
    assert d.feed(0.05, 0.1) == "idle"
    assert d.feed(0.05, 0.1) == "idle"
    assert d.feed(0.05, 0.1) == "speaking"


def test_short_burst_ignored():
    d = TurnDetector(threshold=0.02, start_speech=0.4, end_silence=1.0)
    assert d.feed(0.05, 0.1) == "idle"
    assert d.feed(0.05, 0.1) == "idle"
    assert d.feed(0.0, 0.5) == "idle"


def test_turn_ends_after_silence():
    d = TurnDetector(threshold=0.02, start_speech=0.4, end_silence=1.0)
    for _ in range(4):
        d.feed(0.05, 0.1)
    for _ in range(9):
        assert d.feed(0.0, 0.1) == "speaking"
    assert d.feed(0.0, 0.1) == "turn_end"
    assert d.feed(0.0, 0.1) == "idle"


def test_exit_words():
    assert "salir" in EXIT_WORDS
    assert "chau" in EXIT_WORDS
    assert "hola" not in EXIT_WORDS


def test_extract_command_variants():
    from core.call import _extract_command
    assert _extract_command("claudio pone musica")[0] is True
    found, cmd = _extract_command("hey charly, pausá la música")
    assert found is True and "paus" in cmd and "charly" not in cmd
    found, cmd = _extract_command("sharvis qué hora es")
    assert found is True and "sharvis" not in cmd
    found, _ = _extract_command("che, qué bueno está el día")
    assert found is False
    found, cmd = _extract_command("claudio")
    assert found is True and cmd == ""


def test_banner_exists():
    from pathlib import Path
    banner = Path(__file__).resolve().parent.parent / "assets" / "banner.txt"
    assert banner.exists()
    lines = banner.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 10
    assert all(len(line) <= 70 for line in lines)
    assert any("CLAUDIO" in line.replace(" ", "") for line in lines)


def test_extract_command_accents():
    from core.call import _extract_command
    found, cmd = _extract_command("oye claudio, poné música")
    assert found is True and "pon" in cmd and "claudio" not in cmd
    found, cmd = _extract_command("hey chárly apaga la luz")
    assert found is True and "charly" not in cmd and "apaga" in cmd


def test_say_mutes_and_unmutes():
    import asyncio
    from unittest.mock import AsyncMock

    from core.call import CallLoop
    tts = AsyncMock()
    tts.execute_tool.return_value = {"ok": True}
    loop = CallLoop(None, None, tts)
    assert loop._muted is False
    asyncio.run(loop._say("hola"))
    assert loop._muted is False
    tts.execute_tool.assert_called_once()


def test_wake_debouncer():
    from core.wake import WakeDebouncer
    d = WakeDebouncer(required_hits=2, window_seconds=1.5, cooldown_seconds=5.0)
    assert d.feed(False, 0.0) is False
    assert d.feed(True, 0.0) is False
    assert d.feed(True, 0.5) is True
    assert d.feed(True, 1.0) is False
    assert d.feed(True, 6.0) is False
    assert d.feed(True, 6.5) is True


def test_vosk_spotter_missing_model(tmp_path):
    from core.wake import VoskSpotter
    s = VoskSpotter(model_dir=str(tmp_path / "nope"))
    assert s.ready is False
    try:
        s.start()
        raise AssertionError("deberia haber fallado")
    except RuntimeError as e:
        assert "Vosk" in str(e)


def test_wake_gate():
    from core.call import _wake_gate
    assert _wake_gate(True, True, "claudio pon música", 0) == ("process", "pon musica", 0)
    assert _wake_gate(True, True, "claudio", 0)[0] == "ask"
    assert _wake_gate(True, False, "hola qué tal", 0) == ("ignore", "hola qué tal", 1)
    assert _wake_gate(True, False, "hola qué tal", 2)[0] == "process"
    assert _wake_gate(False, False, "hola", 0)[0] == "ignore"
    assert _wake_gate(False, False, "claudio hola", 0)[0] == "process"
    assert _wake_gate(True, False, "", 2)[0] == "ignore"
    loop = "Algún día, Otro beso, Nostalgia. " * 6
    assert _wake_gate(True, False, loop, 2)[0] == "ignore"
    # El fallback cada 3 turnos solo vale si parece habla real.
    assert _wake_gate(True, False, "Gracias.", 2)[0] == "ignore"
    assert _wake_gate(True, False, "hola qué tal amigo", 2)[0] == "process"


def test_extract_command_solo_despues_de_claudio():
    from core.call import _extract_command
    found, cmd = _extract_command("estaba hablando de otra cosa claudio poné música")
    assert found is True and cmd == "pone musica"  # sin acentos por diseño
    found, cmd = _extract_command("C. L. A. U. D. I. O. abri discord")
    assert found is True and "claudio" not in cmd and "abri discord" in cmd
    assert _extract_command("hola qué tal") == (False, "hola qué tal")


def test_skip_quiet_turn():
    from core.call import _skip_quiet_turn
    assert _skip_quiet_turn(True, False, 0.8, 1) is True
    assert _skip_quiet_turn(True, False, 0.8, 0) is False
    assert _skip_quiet_turn(True, True, 0.8, 3) is False
    assert _skip_quiet_turn(True, False, 2.5, 3) is False
    assert _skip_quiet_turn(False, False, 0.8, 3) is False


def test_extract_command_spelled_claudio():
    from core.call import _extract_command
    found, cmd = _extract_command("C. L. A. U. D. I. O. abri discord")
    assert found is True and "claudio" not in cmd and "abri discord" in cmd
    found, _ = _extract_command("hola como estas")
    assert found is False


def test_extract_command_typo_claudio():
    from core.call import _extract_command
    found, cmd = _extract_command("che carvis abri chrome")
    assert found is True and "abri chrome" in cmd
