from core.config import get_settings, settings


def test_config_loads():
    s = get_settings()
    assert s is not None
    assert s.claudio.name == "CLAUDIO"
    assert s.core.model_main == "llama3.2:3b"  # chico, entra entero en VRAM


def test_vault_path_resolves():
    assert settings.memory.vault_path.exists()
    assert settings.memory.vault_path.is_absolute()


def test_plugins_enabled():
    assert "stt_whisper" in settings.plugins.enabled
    assert "pc_control" in settings.plugins.enabled