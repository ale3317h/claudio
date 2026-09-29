from pathlib import Path
from typing import Any, Dict, List, Tuple, Type

from pydantic import field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)


class ClaudioSettings(BaseSettings):
    name: str = "CLAUDIO"
    language: str = "es"
    wake_words: List[str] = ["claudio", "hey claudio"]
    response_language: str = "es"


class CoreSettings(BaseSettings):
    backend: str = "groq"   # groq (gratis, rapido) | gemini | ollama (local, privado)
    fallbacks: List[str] = ["gemini", "ollama"]
    model_main: str = "llama3.2:3b"       # usado si backend=ollama (chico, entra en VRAM)
    model_fast: str = "llama3.2:3b"       # usado si backend=ollama
    model_coder: str = "llama3.1:8b"      # usado si backend=ollama
    groq_model_main: str = "openai/gpt-oss-120b"   # NO usar llama-3.3-70b-versatile: da 404 en plan gratuito
    groq_model_fast: str = "openai/gpt-oss-20b"    # gratis, rapidisimo
    gemini_model_main: str = "gemini-flash-latest"
    gemini_model_fast: str = "gemini-flash-lite-latest"
    openrouter_model_main: str = "nvidia/nemotron-3-super-120b-a12b:free"
    openrouter_model_fast: str = "liquid/lfm-2.5-2.6b:free"
    dream_model_main: str = "auto"
    dream_model_fast: str = "auto"
    github_model_main: str = "openai/gpt-4o-mini"
    github_model_fast: str = "openai/gpt-4o-mini"
    meta_model_main: str = "muse-spark-1.1"  # Meta Model API (OpenAI-compatible)
    meta_model_fast: str = "muse-spark-1.1"
    max_context_tokens: int = 4096
    temperature: float = 0.2


class MemorySettings(BaseSettings):
    vault_path: Path = Path(".")
    embedding_model: str = "nomic-embed-text"
    chunk_size: int = 512
    chunk_overlap: int = 50
    top_k: int = 3
    semantic_weight: float = 0.7
    episodic_weight: float = 0.3

    @field_validator("vault_path", mode="before")
    @classmethod
    def resolve_vault_path(cls, v: Any) -> Path:
        path = Path(v).expanduser().resolve()
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
        return path


class VoiceSettings(BaseSettings):
    stt_backend: str = "groq"   # groq (rapido, gratis) | local (privado, CPU)
    stt_model: str = "large-v3-turbo"   # modelo local (fallback)
    stt_groq_model: str = "whisper-large-v3-turbo"  # whisper-large-v3 = mas preciso, mas lento
    stt_device: str = "cpu"
    stt_compute_type: str = "int8"
    tts_engine: str = "edge"
    tts_voice: str = "es-MX-JorgeNeural"
    tts_rate: str = "+5%"
    tts_pitch: str = "-8Hz"
    sample_rate: int = 16000
    vad_threshold: float = 0.02
    silence_seconds: float = 1.2
    max_record_seconds: float = 30.0
    noise_suppress: bool = True  # supresion de ruido del mic (pasa-altos + compuerta)
    noise_gate_db: float = -38.0  # umbral: tramas bajo esto se atenúan al 12%
    wake_enabled: bool = True
    wake_keyword: str = "claudio"
    wake_sensitivity: float = 0.6
    # Tecla push-to-talk en modo voz: pause | numlock | scroll | insert | rctrl | 0xNN.
    # OJO: PAUSE no se detecta en algunos teclados (tecla especial sin release
    # por hardware); si no responde, usar el probe (scripts/probe_ptt.py).
    ptt_key: str = "numlock"


class PluginPermission(str):
    FILE_READ = "FILE_READ"
    FILE_WRITE = "FILE_WRITE"
    NETWORK = "NETWORK"
    SYSTEM = "SYSTEM"
    AUDIO = "AUDIO"
    SHELL = "SHELL"
    APP_LAUNCH = "APP_LAUNCH"


class PluginsSettings(BaseSettings):
    enabled: List[str] = [
        "stt_whisper",
        "tts_edge",
        "pc_control",
        "web_search",
        "calendar",
        "tasks",
        "obsidian",
        "system",
    ]
    auto_load: bool = True
    permissions: Dict[str, List[str]] = {
        "pc_control": ["FILE_READ", "FILE_WRITE", "APP_LAUNCH", "SHELL"],
        "web_search": ["NETWORK"],
        "calendar": ["NETWORK", "FILE_READ"],
    }


class SecuritySettings(BaseSettings):
    confirm_destructive: bool = True
    allowlist_paths: List[str] = ["~/Documents", "~/Downloads", "~/Desktop"]
    blocklist_apps: List[str] = ["regedit", "cmd", "powershell"]
    audit_log: bool = True


class ObsidianSettings(BaseSettings):
    daily_note_folder: str = "daily-notes"
    templates_folder: str = "templates"
    tasks_format: str = "obsidian-tasks"
    graph_enabled: bool = True


class PcControlSettings(BaseSettings):
    allow_shell: bool = False


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        yaml_file="config.yaml",
        yaml_file_encoding="utf-8",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        nested_model_default_partial_update=True,
    )

    claudio: ClaudioSettings = ClaudioSettings()
    core: CoreSettings = CoreSettings()
    memory: MemorySettings = MemorySettings()
    voice: VoiceSettings = VoiceSettings()
    plugins: PluginsSettings = PluginsSettings()
    security: SecuritySettings = SecuritySettings()
    obsidian: ObsidianSettings = ObsidianSettings()
    pc_control: PcControlSettings = PcControlSettings()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        vault_yaml = Path("config.yaml")
        if not vault_yaml.is_absolute():
            candidates = [Path.cwd() / "config.yaml", Path(__file__).resolve().parent.parent / "config.yaml"]
            for candidate in candidates:
                if candidate.exists():
                    vault_yaml = candidate
                    break
        return (
            init_settings,
            YamlConfigSettingsSource(settings_cls, yaml_file=vault_yaml),
            env_settings,
            dotenv_settings,
            file_secret_settings,
        )


settings = Settings()


def get_settings() -> Settings:
    return settings