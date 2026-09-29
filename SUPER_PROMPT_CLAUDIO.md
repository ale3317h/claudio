# 🤖 SUPER PROMPT: DESARROLLO DE CLAUDIO - ASISTENTE IA TIPO IRON MAN

---

## 🎯 TU MISIÓN

Eres un **Ingeniero Senior de IA/ML + Arquitecto de Sistemas + Experto en Python** encargado de construir **CLAUDIO**: un asistente personal tipo Iron Man que opere 100% local, use **Obsidian como memoria persistente (RAG)**, tenga **arquitectura modular de plugins/skills**, y soporte **voz nativa (STT/TTS)**.

Tu deliverable: **Código funcional, probado, documentado en el vault, listo para usar**.

---

## 📋 CONTEXTO COMPLETO (LEE `agents.md` PRIMERO)

El archivo `agents.md` en la raíz del vault contiene **TODA la especificación técnica**:
- Arquitectura modular (core + plugins + agents)
- Especificaciones de cada módulo (brain, memory, voice, planner, executor)
- 8 plugins base (STT, TTS, PC Control, Web Search, Calendar, Tasks, Obsidian, System)
- 6 agentes especializados (Researcher, Executor, Planner, Analyst, Creative, Guardian)
- Stack tecnológico completo (LangGraph, Ollama, faster-whisper, Piper, ChromaDB, etc.)
- Configuración (config.yaml)
- Flujos principales
- Roadmap 12 semanas
- Métricas de éxito
- Seguridad y privacidad

**LEE `agents.md` AHORA. ES TU BIBLIA.**

---

## 🏁 EMPIEZA AQUÍ - FASE 1: FUNDACIÓN

### Setup Inicial (ejecuta en orden)

```bash
# 1. Entorno
cd CLAUDIO_Vault
python -m venv .venv
.venv\Scripts\activate  # Windows
pip install --upgrade pip

# 2. Dependencias core
pip install langgraph langchain-core langchain-ollama
pip install chromadb pydantic pydantic-settings pyyaml
pip install loguru structlog watchdog
pip install pytest pytest-asyncio pytest-cov ruff mypy

# 3. Dependencias voz (opcional: instala cuando toque fase 2)
# pip install faster-whisper piper-tts openwakeword sounddevice

# 4. Dependencias PC/Automation (fase 2)
# pip install uiautomation pywinauto

# 5. Dependencias web (fase 3)
# pip install trafilatura httpx duckduckgo-search

# 6. Ollama (requerido)
# winget install Ollama.Ollama
# ollama pull llama3.1:8b llama3.1:70b qwen2.5-coder:32b nomic-embed-text
```

---

## 📁 ESTRUCTURA DE ARCHIVOS A CREAR

```
CLAUDIO_Vault/
├── core/
│   ├── __init__.py
│   ├── config.py          # Pydantic Settings (config.yaml)
│   ├── brain.py           # LangGraph orchestrator
│   ├── memory.py          # RAG + ChromaDB + vault indexer
│   ├── voice.py           # STT/TTS pipeline (stub fase 1)
│   ├── planner.py         # ReAct planner
│   ├── executor.py        # Tool executor
│   ├── events.py          # Event bus
│   └── main.py            # Entry point CLI
├── plugins/
│   ├── __init__.py
│   ├── base.py            # PluginBase abstract class
│   ├── registry.py        # Plugin loader/registry
│   └── manifest.yaml      # Schema validación
├── agents/
│   ├── __init__.py
│   ├── base.py            # AgentBase
│   ├── researcher.py
│   ├── executor.py
│   ├── planner.py
│   └── router.py          # Intent -> Agent routing
├── config.yaml            # Configuración (ver agents.md)
├── requirements.txt       # Generado
├── .env.example           # Template secrets
├── .gitignore
├── README.md              # Setup + uso
├── tests/
│   ├── test_config.py
│   ├── test_memory.py
│   ├── test_brain.py
│   └── test_plugins.py
└── scripts/
    ├── reindex_vault.py
    ├── create_skill.py
    └── run_claudio.py
```

---

## ✅ TAREAS FASE 1 - ENTREGABLES MÍNIMOS

### 1. `core/config.py` - Configuración Tipada
```python
# Pydantic Settings que carga config.yaml + .env
# Valida: models, paths, plugins, security, voice, memory
# Propiedades: vault_path (Path), model_main (str), embedding_model (str), etc.
```

### 2. `core/events.py` - Event Bus Simple
```python
# Pub/Sub async para desacoplar core <-> plugins
# Eventos: on_user_input, on_intent_classified, on_tool_call, on_response, on_error
# Decorador @event_handler("event_name")
```

### 3. `plugins/base.py` - PluginBase Abstracto
```python
class PluginBase(ABC):
    name: str
    version: str
    description: str
    dependencies: List[str]
    permissions: List[Permission]  # Enum: FILE_READ, NETWORK, SYSTEM, AUDIO, SHELL
    
    @abstractmethod
    async def initialize(self, core: 'CLAUDIOCore') -> None
    @abstractmethod
    async def shutdown(self) -> None
    @abstractmethod
    def get_tools(self) -> List[Tool]  # OpenAI tool schema
    @abstractmethod
    def get_intents(self) -> List[IntentPattern]  # regex + entities
```

### 4. `plugins/registry.py` - Plugin Loader
```python
# Descubre plugins en plugins/*/
# Valida manifest.yaml (schema)
# Resuelve dependencias (topological sort)
# Inicializa en orden, maneja errores
# Expone: get_plugin(name), get_all_tools(), get_all_intents()
```

### 5. `core/memory.py` - Sistema RAG
```python
class MemorySystem:
    # ChromaDB persistent client en data/chromadb/
    # Collection: "vault_semantic", "vault_episodic", "vault_procedural"
    # Embeddings: OllamaEmbeddings(model="nomic-embed-text")
    # Chunking: MarkdownHeaderTextSplitter (headers -> chunks)
    # Watchdog: re-indexa archivo .md modificado
    # Methods: add_document, search, delete, reindex_vault()
```

### 6. `core/brain.py` - LangGraph Skeleton
```python
# State: CLAUDIOState (TypedDict)
# Nodes: perceive -> understand -> retrieve -> plan -> execute -> verify -> synthesize -> speak -> learn
# Compile: graph = StateGraph(CLAUDIOState).compile(checkpointer=MemorySaver())
# Streaming: async for chunk in graph.astream(state): yield chunk
# Tools: inyectados desde PluginRegistry.get_all_tools()
```

### 6. `core/main.py` - CLI Entry Point
```python
# Typer/Click CLI
# Commands: run (--voice/--text), reindex, test, create-skill
# Setup: logging, config, plugin registry, memory, brain
# Loop: while True: input -> brain.astream -> print/tts
```

### 7. `config.yaml` - Configuración Completa
```yaml
# Copia exacta de agents.md sección CONFIGURACIÓN
```

### 8. Tests Básicos
```python
# test_config.py: carga config, valida paths
# test_memory.py: add + search + delete documentos
# test_plugins.py: load dummy plugin, get tools/intents
```

---

## 🎯 CRITERIOS DE ACEPTACIÓN FASE 1

| Criterio | Verificación |
|----------|--------------|
| Config carga sin errores | `python -c "from core.config import settings; print(settings.vault_path)"` |
| Plugin registry descubre plugins | `python -c "from plugins.registry import registry; print(registry.list_plugins())"` |
| Memory indexa vault | `python scripts/reindex_vault.py` -> ChromaDB tiene docs |
| Brain compila grafo | `python -c "from core.brain import graph; print(graph.get_graph().draw_ascii())"` |
| CLI funciona | `python -m core.main --text "hola"` -> respuesta (mock LLM) |
| Tests pasan | `pytest tests/ -v` -> 100% pass |
| Lint limpio | `ruff check . && mypy core/ plugins/` -> 0 errors |

---

## 🔑 PRINCIPIOS NO NEGOCIABLES

1. **Obsidian = Source of Truth**: Todo conocimiento persiste en `.md` en el vault. ChromaDB es solo índice.
2. **Tokens son caros**: Siempre `retrieve` antes de `generate`. Cache embeddings. Chunking semántico.
3. **Modular de verdad**: Cada plugin en su carpeta, `__init__.py` + `manifest.yaml`, cero acoplamiento.
4. **Type hints everywhere**: `mypy --strict` debe pasar. Pydantic para validación runtime.
5. **Async first**: Todo I/O (LLM, STT, TTS, FS, Network) es `async/await`.
6. **Streaming UX**: Respuesta token-by-token, TTS chunked, no esperes a completar.
7. **Privacidad**: Cero red salvo plugins explícitos (web_search, calendar). Logs locales.
8. **Observabilidad**: Structured logs (structlog) + métricas (latencia, tokens, errors).

---

## 🧪 CÓMO TESTEAR SIN LLM REAL (MOCKS)

```python
# tests/conftest.py
import pytest
from unittest.mock import AsyncMock

@pytest.fixture
def mock_llm():
    llm = AsyncMock()
    llm.ainvoke.return_value = "Respuesta mock"
    llm.astream.return_value = async_iter(["Respuesta ", "mock"])
    return llm

# En brain.py: inyecta LLM via config, usa mock en tests
```

---

## 📝 DOCUMENTACIÓN EN VAULT (OBLIGATORIO)

Cada decisión arquitectónica -> nota en `docs/decisions/YYYY-MM-DD-decision.md`:
```markdown
---
date: 2024-01-15
decision: "Usar LangGraph vs LangChain AgentExecutor"
status: accepted
---

# Contexto
...

# Decisión
...

# Consecuencias
...
```

---

## ⚡ COMANDOS ÚTILES DURANTE DESARROLLO

```bash
# Re-indexar vault completo
python scripts/reindex_vault.py --full

# Test brain con input texto
python -m core.main --text "¿Qué hora es?"

# Ver grafo LangGraph
python -c "from core.brain import graph; print(graph.get_graph().draw_ascii())"

# Crear skill template
python scripts/create_skill.py --name mi_skill --category automation

# Debug plugins
python -c "from plugins.registry import registry; registry.load_all(); print(registry.get_all_tools())"

# Logs bonitos
tail -f data/logs/claudio.log | jq
```

---

## 🚀 SIGUIENTES FASES (REFERENCIA)

| Fase | Enfoque | Archivos Clave |
|------|---------|----------------|
| 2 | Voz + PC | `core/voice.py`, `plugins/stt_whisper/`, `plugins/tts_piper/`, `plugins/pc_control/` |
| 3 | Skills Core | `plugins/web_search/`, `plugins/calendar/`, `plugins/tasks/`, `plugins/obsidian/`, `plugins/system/` |
| 4 | Agentes | `agents/researcher.py`, `agents/executor.py`, `agents/planner.py`, `agents/router.py` |
| 5 | Memoria Avanzada | `core/memory.py` (fact extraction, episodic, procedural) |
| 6 | Polish | GUI, skill marketplace, voice cloning, mobile |

---

## 💡 TIPS DE ORO PARA TI (EL AGENTE DESARROLLADOR)

1. **No escribas todo de golpe**: Incremental. Core -> 1 plugin -> test -> siguiente.
2. **Usa el vault**: Guarda specs, decisions, logs, test results en `.md`. Es tu memoria.
3. **Mock LLM en tests**: Tests unitarios no llaman a Ollama. Usa `instructor` para structured output testing.
4. **Streaming desde día 1**: `astream` en brain, `async for` en main. UX real-time.
5. **Permissions early**: Plugin `pc_control` necesita `SHELL` permission. Valida en registry.
6. **Config driven**: Hardcodea CERO. Todo en `config.yaml` + `.env`.
7. **Error handling**: `try/except` en cada node del grafo. `events.emit("on_error", ...)`.
8. **Commit often**: `git add -A && git commit -m "feat: ..."` cada feature completa.

---

## 🎬 TU PRIMER PASO AHORA

```bash
cd CLAUDIO_Vault
cat agents.md                    # LEE LO COMPLETO
cat config.yaml                  # VERIFICA CONFIG
# Empieza con core/config.py
```

**¡A construir CLAUDIO!** 🤖⚡

---

*Prompt version: 1.0 | Para: Cualquier IA con capacidades de coding | Contexto: agents.md en mismo vault*