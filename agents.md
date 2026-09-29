# CLAUDIO - Asistente IA Personal Tipo Iron Man
## agents.md - Contexto Completo para Agentes de Desarrollo

---

## 🎯 VISIÓN GENERAL

**Objetivo**: Crear un asistente personal tipo CLAUDIO (Iron Man) que opere localmente, use Obsidian como base de conocimiento/memoria persistente, y tenga arquitectura modular de plugins/skills.

**Filosofía**: 
- **Privacidad first**: Todo local, nada en la nube salvo APIs explícitas
- **Token efficiency**: Obsidian como memoria externa (RAG sobre vault)
- **Extensible**: Sistema de plugins/skills tipo VS Code extensions
- **Voz nativa**: STT + TTS en tiempo real
- **Autonomía**: Ejecuta tareas complejas multi-paso

---

## 🏗️ ARQUITECTURA MODULAR

```
CLAUDIO_Vault/
├── .obsidian/           # Config Obsidian
├── agents/              # Definiciones de agentes especializados
├── core/                # Núcleo del sistema
│   ├── __init__.py
│   ├── brain.py         # Orquestador principal (LangGraph)
│   ├── memory.py        # RAG sobre vault Obsidian
│   ├── voice.py         # STT/TTS pipeline
│   ├── planner.py       # Planificación de tareas complejas
│   ├── executor.py      # Ejecución de acciones
│   └── events.py        # Event bus para plugins
├── plugins/             # Skills/Plugins instalables
│   ├── __init__.py
│   ├── base.py          # PluginBase abstract class
│   ├── registry.py      # Plugin registry & loader
│   ├── stt_whisper/     # Speech-to-Text (faster-whisper)
│   ├── tts_piper/       # Text-to-Speech (Piper/Coqui)
│   ├── pc_control/      # Automatización Windows (pywinauto, uiautomation)
│   ├── web_search/      # Búsqueda web (SearXNG, DuckDuckGo, Brave)
│   ├── calendar/        # Google Cal / CalDAV / Local ics
│   ├── tasks/           # Gestión TODOs (Obsidian tasks plugin format)
│   ├── obsidian/        # Read/write vault, graph queries
│   ├── system/          # Info sistema, procesos, archivos
│   └── skills/          # Skills compuestas multi-plugin
├── docs/                # Documentación técnica
├── data/                # Datos persistentes (sqlite, embeddings, cache)
├── templates/           # Plantillas para notas, skills, agents
└── daily-notes/         # Notas diarias automáticas
```

---

## 🧠 CORE MODULES - ESPECIFICACIONES

### 1. brain.py - Orquestador Principal (LangGraph)
```python
# Estado del grafo
class CLAUDIOState(TypedDict):
    user_input: str                    # Input crudo (voz/texto)
    intent: Intent                     # Intención clasificada
    context: ConversationContext       # Historial + memoria relevante
    plan: List[ActionStep]             # Plan de ejecución
    current_step: int                  # Paso actual
    results: Dict[str, Any]            # Resultados de pasos
    response: str                      # Respuesta final
    metadata: Dict                     # Tokens, tiempo, confidence

# Nodos del grafo
1. PERCEIVE     -> STT + normalización
2. UNDERSTAND   -> LLM: intent classification + entity extraction
3. RETRIEVE     -> RAG sobre vault (memoria semántica + episódica)
4. PLAN         -> LLM: descomponer en ActionSteps (ReAct)
5. EXECUTE      -> Ejecutar plugins/tools en orden/paralelo
6. VERIFY       -> Validar resultados, re-plan si falla
7. SYNTHESIZE   -> LLM: generar respuesta natural
8. SPEAK        -> TTS + guardar en memoria
9. LEARN        -> Actualizar embeddings, extraer facts
```

### 2. memory.py - Sistema de Memoria (RAG sobre Vault)
```python
# Tres capas de memoria
SEMANTIC_MEMORY:   # Conocimiento general, facts, preferencias
    - ChromaDB/FAISS sobre vault/*.md
    - Embeddings: nomic-embed-text / bge-m3
    - Chunking: semantic (markdown headers)

EPISODIC_MEMORY:   # Conversaciones, eventos, "qué pasó ayer"
    - daily-notes/YYYY-MM-DD.md + JSONL append-only
    - Vector search + temporal queries

PROCEDURAL_MEMORY: # Skills, workflows, "cómo hacer X"
    - plugins/skills/*.yaml + code
    - Few-shot examples en vault/skills/examples/
```

### 3. voice.py - Pipeline Voz
```python
# STT: faster-whisper (large-v3, cpu/int8) o whisper.cpp
# VAD: silero-vad para detección voz en tiempo real
# Wake word: "CLAUDIO" / "Hey CLAUDIO" (openWakeWord / Porcupine)
# TTS: Piper (rápido, multi-voz) o Coqui XTTS v2 (clonación voz)
# Audio: sounddevice / pyaudio, 16kHz mono
```

### 4. planner.py - Planificador ReAct + Tool Use
```python
# Herramientas disponibles (Tool Schema OpenAI)
tools = [
    {"name": "search_web", "description": "Buscar info actualizada"},
    {"name": "read_note", "description": "Leer nota Obsidian"},
    {"name": "write_note", "description": "Escribir/actualizar nota"},
    {"name": "run_script", "description": "Ejecutar Python/PowerShell"},
    {"name": "control_pc", "description": "Automatizar Windows"},
    {"name": "manage_calendar", "description": "CRUD eventos"},
    {"name": "manage_tasks", "description": "CRUD tareas"},
    {"name": "system_info", "description": "Info HW/SW/red"},
]

# Plan = List[ActionStep(tool, args, depends_on, retry_policy)]
```

---

## 🔌 PLUGINS / SKILLS - ESPECIFICACIONES

### PluginBase (abstract)
```python
class PluginBase(ABC):
    name: str
    version: str
    description: str
    dependencies: List[str]           # Otros plugins requeridos
    permissions: List[Permission]     # FILE_READ, NETWORK, SYSTEM, AUDIO
    
    @abstractmethod
    async def initialize(self, core: 'CLAUDIOCore') -> None: ...
    
    @abstractmethod
    async def shutdown(self) -> None: ...
    
    @abstractmethod
    def get_tools(self) -> List[Tool]: ...
    
    @abstractmethod
    def get_intents(self) -> List[IntentPattern]: ...
```

### Plugin: stt_whisper
- **Motor**: faster-whisper (CTranslate2), modelo large-v3 / distil-large-v3
- **Modos**: streaming (VAD) + push-to-talk
- **Idiomas**: es, en, auto-detect
- **Output**: texto + timestamps + confidence + language

### Plugin: tts_piper
- **Motor**: Piper TTS (ONNX, CPU real-time)
- **Voces**: es_ES-carlfm-x_low, en_US-lessac-medium, custom voice cloning
- **Streaming**: chunked audio output para baja latencia
- **SSML**: soporte básico (rate, pitch, volume, emphasis)

### Plugin: pc_control
- **Backend**: uiautomation (Windows UI Automation) + pywinauto fallback
- **Acciones**: 
  - App: launch, focus, close, list_windows
  - File: open, search, copy, move, delete, create_shortcut
  - Input: type_text, hotkey, click, scroll
  - Shell: run_command (PowerShell/CMD), elevate
  - Monitor: screenshot, ocr_region, get_window_tree
- **Seguridad**: confirmación para acciones destructivas, allowlist paths/apps

### Plugin: web_search
- **Motores**: SearXNG (self-hosted), DuckDuckGo HTML, Brave Search API
- **Features**: 
  - Búsqueda + scraping (trafilatura/readability)
  - Resumen automático (LLM) con citas
  - Búsqueda por fecha, sitio, tipo archivo
  - Cache en vault/data/web_cache/

### Plugin: calendar
- **Backends**: Google Calendar API, CalDAV (Nextcloud/ownCloud), Local .ics
- **Operaciones**: create, read, update, delete, list, find_conflicts
- **Natural language**: "reunión mañana 10am con Juan" -> parse + confirm

### Plugin: tasks
- **Formato**: Obsidian Tasks plugin compatible (`- [ ] #tag 📅 2024-01-15 ⏳ 2024-01-10`)
- **Storage**: vault/daily-notes/ + vault/tasks/ proyectos
- **Features**: recurrencia, dependencias, subtareas, filtros Dataview

### Plugin: obsidian
- **API**: Vault abstraction (read/write/list/search)
- **Graph queries**: links, backlinks, tags, dataviewjs
- **Templates**: Jinja2 para notas estructuradas
- **Indexado**: watchdog para re-indexar embeddings en cambios

### Plugin: system
- **Info**: CPU, RAM, GPU, disco, red, batería, procesos
- **Control**: sleep, shutdown, restart, volume, brightness
- **Monitoreo**: alerts (temp alta, disco lleno, servicio caído)

---

## 🤖 AGENTES ESPECIALIZADOS (agents/)

Cada agente es un sub-grafo LangGraph con tools específicos:

| Agente | Responsabilidad | Tools Principales |
|--------|-----------------|-------------------|
| **Researcher** | Investigación profunda web + vault | web_search, read_note, write_note |
| **Executor** | Automatización PC, scripts, archivos | pc_control, run_script, system |
| **Planner** | Agenda, tareas, recordatorios | calendar, tasks, obsidian |
| **Analyst** | Análisis datos, logs, código | read_note, run_script, web_search |
| **Creative** | Escritura, código, ideas | web_search, write_note, run_script |
| **Guardian** | Seguridad, privacidad, backups | system, obsidian, pc_control |

**Orquestación**: brain.py routea al agente según intent + complejidad

---

## 📦 STACK TECNOLÓGICO

| Capa | Tecnología | Justificación |
|------|------------|---------------|
| **Orquestación** | LangGraph (Python) | Stateful, streaming, human-in-loop, persistence |
| **LLM Local** | Ollama (Llama 3.1 8B/70B, Qwen2.5-Coder) | Privacidad, costo cero, tool calling nativo |
| **LLM Cloud (opt)** | OpenAI/Anthropic/Gemini | Fallback para tareas complejas |
| **Embeddings** | nomic-embed-text / bge-m3 (Ollama) | Multilingüe, buenos en código |
| **Vector DB** | ChromaDB (local, persistent) | Simple, filtro metadata, HNSW |
| **STT** | faster-whisper (large-v3, int8) | Preciso, rápido CPU, streaming |
| **TTS** | Piper (ONNX) + XTTS v2 (opcional) | Real-time, multi-voz, clonación |
| **Wake Word** | openWakeWord / Porcupine | Offline, personalizable |
| **PC Automation** | uiautomation + pywinauto | Nativo Windows, accesibilidad |
| **Web Search** | SearXNG (Docker) + trafilatura | Privado, sin API keys, configurable |
| **Calendar** | CalDAV (Radicale/Nextcloud) + Google API | Estándar abierto + cloud opcional |
| **Config** | Pydantic Settings (YAML + env) | Type-safe, validación, secrets |
| **Logging** | loguru + structlog | Structured, rotating, context |
| **Testing** | pytest + pytest-asyncio | Async, fixtures, coverage |

---

## ⚙️ CONFIGURACIÓN (config.yaml)

```yaml
claudio:
  name: "CLAUDIO"
  language: "es"
  wake_words: ["claudio", "hey claudio"]
  response_language: "es"
  
core:
  model_main: "llama3.1:70b"           # Ollama model
  model_fast: "llama3.1:8b"            # Para clasificación rápida
  model_coder: "qwen2.5-coder:32b"     # Código/scripts
  max_context_tokens: 128000
  temperature: 0.3
  
memory:
  vault_path: "./"                      # Root del vault Obsidian
  embedding_model: "nomic-embed-text"
  chunk_size: 512
  chunk_overlap: 50
  top_k: 5
  semantic_weight: 0.7
  episodic_weight: 0.3
  
voice:
  stt_model: "large-v3"
  stt_device: "cpu"
  stt_compute_type: "int8"
  tts_voice: "es_ES-carlfm-x_low"
  tts_speed: 1.1
  sample_rate: 16000
  vad_threshold: 0.5
  
plugins:
  enabled:
    - stt_whisper
    - tts_piper
    - pc_control
    - web_search
    - calendar
    - tasks
    - obsidian
    - system
  auto_load: true
  permissions:
    pc_control: ["FILE_READ", "FILE_WRITE", "APP_LAUNCH", "SHELL"]
    web_search: ["NETWORK"]
    calendar: ["NETWORK", "FILE_READ"]
    
security:
  confirm_destructive: true
  allowlist_paths: ["~/Documents", "~/Downloads", "~/Desktop"]
  blocklist_apps: ["regedit", "cmd", "powershell"]  # unless elevated
  audit_log: true
  
obsidian:
  daily_note_folder: "daily-notes"
  templates_folder: "templates"
  tasks_format: "obsidian-tasks"
  graph_enabled: true
```

---

## 🔄 FLUJOS PRINCIPALES

### Flujo: Comando de Voz "CLAUDIO, busca info sobre X y guárdalo en mi vault"
```
1. WAKE WORD detected -> start recording
2. VAD detects end of speech -> STT transcribe
3. brain: UNDERSTAND -> intent=RESEARCH_AND_SAVE, entities={topic: "X"}
4. brain: RETRIEVE -> search vault for existing notes on X
5. brain: PLAN -> [search_web(topic), synthesize, write_note]
6. Researcher agent: search_web -> scrape top 5 -> LLM summarize
7. Executor: write_note(vault/notes/X.md, template=research)
8. brain: SYNTHESIZE -> "He guardado un resumen en tu vault"
9. TTS speak response
10. LEARN -> update embeddings, extract facts to semantic memory
```

### Flujo: "CLAUDIO, abre VS Code en mi proyecto Python"
```
1. STT -> intent=PC_CONTROL, action=launch_app, target="vscode", args=["proyecto"]
2. pc_control: find_vscode_path() -> launch with cwd=~/proyecto
3. Verify window focused -> report success
4. TTS: "VS Code abierto en tu proyecto"
```

### Flujo: "CLAUDIO, agenda reunión con María mañana 3pm"
```
1. intent=CALENDAR_CREATE, entities={who: "María", when: "mañana 15:00"}
2. calendar: check_availability -> parse natural date
3. Confirm details with user (voice)
4. calendar: create_event -> sync
5. tasks: create follow-up task "Preparar reunión con María"
6. daily-note: add link to event
```

---

## 📝 TEMPLATES OBSIDIAN (templates/)

### daily-note.md
```markdown
---
date: {{date:YYYY-MM-DD}}
day: {{date:dddd}}
tags: [daily-note]
---

# {{date:YYYY-MM-DD}} {{date:dddd}}

## 🎯 Objetivos del día
- [ ] 

## 📅 Eventos
```dataview
LIST FROM "daily-notes" WHERE date = date(today) SORT time
```

## 💬 Conversaciones con CLAUDIO
```dataview
LIST FROM "daily-notes" WHERE contains(file.name, "claudio-log") SORT file.ctime DESC LIMIT 10
```

## 📝 Notas rápidas
-

## 🔍 Investigación del día
```dataview
LIST FROM "research" WHERE date(created) = date(today)
```

---
*Generado automáticamente por CLAUDIO*
```

### research-note.md
```markdown
---
title: "{{topic}}"
date: {{date:YYYY-MM-DD}}
tags: [research, {{topic_tags}}]
source: "web_search + CLAUDIO synthesis"
---

# {{topic}}

## 📋 Resumen Ejecutivo
{{summary}}

## 🔑 Hallazgos Clave
{% for finding in key_findings %}
- {{finding}}
{% endfor %}

## 📚 Fuentes
{% for source in sources %}
- [{{source.title}}]({{source.url}}) - {{source.date}}
{% endfor %}

## 💡 Ideas / Follow-up
- 

## 🏷️ Tags relacionados
{{related_tags}}
```

### skill-template.yaml
```yaml
name: "{{skill_name}}"
version: "1.0.0"
description: "{{description}}"
author: "CLAUDIO"
category: "automation|research|productivity|system"
triggers:
  - intent: "{{intent_name}}"
    patterns:
      - "{{pattern_1}}"
      - "{{pattern_2}}"
steps:
  - name: "{{step_1}}"
    tool: "{{tool_name}}"
    args: {{args_template}}
    depends_on: []
  - name: "{{step_2}}"
    tool: "{{tool_name}}"
    args: {{args_template}}
    depends_on: ["{{step_1}}"]
output:
  template: "{{response_template}}"
  save_to_vault: true
  vault_path: "{{vault_path}}"
```

---

## 🚀 ROADMAP DE DESARROLLO

### Fase 1: Fundación (Semana 1-2)
- [ ] Setup vault Obsidian + config
- [ ] Core: brain.py (LangGraph skeleton)
- [ ] Memory: ChromaDB + vault indexer
- [ ] Config: Pydantic Settings + config.yaml
- [ ] Plugin system: base, registry, loader
- [ ] Logging + error handling

### Fase 2: Voz + PC Básico (Semana 3-4)
- [ ] STT: faster-whisper integration
- [ ] TTS: Piper integration
- [ ] Wake word: openWakeWord
- [ ] PC Control: uiautomation basics (launch, focus, type)
- [ ] Voice pipeline end-to-end test

### Fase 3: Skills Core (Semana 5-6)
- [ ] Web Search: SearXNG + trafilatura + summarization
- [ ] Calendar: CalDAV + Google API
- [ ] Tasks: Obsidian Tasks format
- [ ] Obsidian plugin: read/write/search/graph
- [ ] System plugin: info + basic control

### Fase 4: Agentes + Orquestación (Semana 7-8)
- [ ] Researcher agent
- [ ] Executor agent
- [ ] Planner agent
- [ ] Multi-agent routing en brain.py
- [ ] ReAct loop con verification

### Fase 5: Memoria + Aprendizaje (Semana 9-10)
- [ ] Semantic memory: auto-extract facts
- [ ] Episodic memory: conversation logs
- [ ] Procedural memory: skill learning from examples
- [ ] Long-term context: summarize old conversations

### Fase 6: Pulido + Features Avanzadas (Semana 11-12)
- [ ] GUI: Tray icon + overlay + settings
- [ ] Skill marketplace: install from vault/git
- [ ] Voice cloning: XTTS v2 integration
- [ ] Mobile companion: Termux/ssh + notifications
- [ ] Benchmarks + optimización tokens

---

## 📊 MÉTRICAS DE ÉXITO

| Métrica | Target |
|---------|--------|
| Latencia voz-respuesta (simple) | < 2s |
| Latencia voz-respuesta (web search) | < 5s |
| Precisión STT (español) | > 95% |
| Naturalidad TTS (MOS) | > 4.0 |
| Precisión intent classification | > 90% |
| Tasa éxito tareas multi-paso | > 85% |
| Tokens/día (con vault RAG) | < 50k |
| Uso RAM (idle) | < 2GB |
| Uso RAM (activo) | < 4GB |

---

## 🔐 SEGURIDAD Y PRIVACIDAD

- **Cero telemetría**: No hay analytics, no hay phone home
- **API Keys**: Solo en `.env` (gitignored), nunca en vault
- **Permisos**: Sistema de capabilities por plugin
- **Auditoría**: Log de todas las acciones destructivas
- **Confirmación**: Voice confirmation para acciones críticas
- **Backup**: Vault versionado con git (auto-commit diario)
- **Encriptación**: Opcional - vault en VeraCrypt/BitLocker

---

## 🛠️ COMANDOS DE DESARROLLO

```bash
# Iniciar CLAUDIO
cd CLAUDIO_Vault
python -m core.main --voice

# Solo texto (debug)
python -m core.main --text

# Instalar skill desde template
python -m plugins.registry create-skill --name mi_skill --category automation

# Re-indexar vault
python -m core.memory reindex --full

# Tests
pytest tests/ -v --cov=core --cov=plugins

# Lint
ruff check . && mypy core/ plugins/

# Docker SearXNG (búsqueda web local)
docker run -d -p 8080:8080 searxng/searxng
```

---

## 📚 RECURSOS Y REFERENCIAS

- **LangGraph**: https://langchain-ai.github.io/langgraph/
- **Ollama**: https://ollama.com/library
- **faster-whisper**: https://github.com/SYSTRAN/faster-whisper
- **Piper TTS**: https://github.com/rhasspy/piper
- **openWakeWord**: https://github.com/dscripka/openWakeWord
- **uiautomation**: https://github.com/yinkaisheng/python-uiautomation
- **SearXNG**: https://docs.searxng.org/
- **Obsidian Tasks**: https://obsidian-tasks-group.github.io/obsidian-tasks/
- **ChromaDB**: https://docs.trychroma.com/
- **Pydantic Settings**: https://docs.pydantic.dev/latest/concepts/pydantic_settings/

---

## 💡 NOTAS PARA EL AGENTE DESARROLLADOR

1. **Empieza simple**: Core + 1 plugin (STT/TTS) -> test loop -> añade plugins
2. **Obsidian es la DB**: No inventes BD separadas, usa vault + ChromaDB
3. **Tokens caros**: Siempre RAG primero, LLM segundo. Cache agresivo.
4. **Voz = UX crítica**: Latencia < 200ms TTS start, streaming audio
5. **Modular de verdad**: Plugin = carpeta con `__init__.py` + `manifest.yaml`
6. **Testing real**: Usa `pytest-asyncio`, mock LLM con `instructor`/structured output
7. **Documenta en vault**: Cada decisión -> nota en `docs/decisions/`
8. **Iteración rápida**: `python -m core.main --text` para debug sin micrófono

---

*Última actualización: {{date:YYYY-MM-DD}}*
*Versión: 1.0.0 - Especificación inicial*