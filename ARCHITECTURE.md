# CLAUDIO â€” Arquitectura real (FASE 0, auditorÃ­a 2026-09-16)

Documento fuente de verdad sobre cÃ³mo funciona el sistema HOY.
Verificado por inspecciÃ³n directa del cÃ³digo, no por la especificaciÃ³n original.

## Stack

| Capa | TecnologÃ­a real |
|------|-----------------|
| Lenguaje | Python 3.12, `asyncio` en todo I/O |
| OrquestaciÃ³n | LangGraph (`StateGraph` + `MemorySaver`), grafo en `core/brain.py` |
| LLM | LangChain: Groq (principal, `openai/gpt-oss-120b` + `openai/gpt-oss-20b`) â†’ Gemini â†’ Ollama local (`llama3.1:8b`). Cadena resiliente con salud persistente en `core/llm.py` |
| STT | `plugins/stt_whisper`: Groq `whisper-large-v3-turbo` (rÃ¡pido) con fallback local `faster-whisper` |
| TTS | `plugins/tts_edge` (Edge, voz `es-AR-TomasNeural`). Gratis, 0 tokens LLM |
| Wake word | `core/wake.py` (`VoskSpotter`, local) + gate por texto en `core/call.py` |
| Memoria semÃ¡ntica | ChromaDB persistente (`data/chromadb`) + embeddings Ollama `nomic-embed-text`, Ã­ndice sobre `*.md` del vault |
| Memoria estructurada | SQLite (`data/claudio.db`: facts, events, backend_health, llm_usage) + `data/preferences.json` (`core/store.py`) |
| Memoria legible | `memoria.md` (hechos), `musica.md` (reglas), `usuario.md`, `daily-notes/` |
| Plugins | Clases `PluginBase` en `plugins/<nombre>/` + `manifest.yaml`, cargadas por `plugins/registry.py` (11 plugins con manifest) |
| Config | `config.yaml` + `.env` vÃ­a Pydantic Settings (`core/config.py`) |
| CLI | Typer (`core/main.py`): `run --text/--voice/--call/--query`, `reindex`, `test`, `lint` |
| Entradas | `CLAUDIO.bat` â†’ `run --text`; atajos Llamada/Voz/Texto en escritorio |

## Pipeline real

```
INPUT (micrÃ³fono / teclado)
 â†“  core/call.py (CallLoop: VAD por energÃ­a, turnos, gate de wake)
 â†“  core/voice.py (VoiceLoop push-to-talk, solo modo --voice)
STT â†’ plugins/stt_whisper.transcribe_array() [+ fix_transcript: ramaâ†’RAMMA, !playâ†’m!play]
 â†“
LLM/CEREBRO â†’ core/brain.py (grafo LangGraph):
 â”‚  perceive (normaliza texto) â†’ route (chat directo vs full)
 â”‚  â”œâ”€ chat â†’ llm_main + memoria RAG â†’ response   (conversaciÃ³n simple, 1 llamada)
 â”‚  â””â”€ full â†’ understand (llm_fast o atajo regex) â†’ retrieve (Chroma top_k=3)
 â”‚       â†’ plan (llm_fast, 1 paso) â†’ execute â†’ verify â†’ replan (mÃ¡x 5 pasos)
 â”‚       â†’ synthesize (llm_main) â†’ speak â†’ learn
 â†“
TOOLS â†’ plugin.execute_tool() (despacho por PluginRegistry; args con placeholders sin
 â†“      resolver se rechazan; comandos Discord se auto-corrigen a m!*)
RESPONSE â†’ evento on_response_synthesized â†’ consola (rich) + TTS
 â†“
TTS â†’ plugins/tts_edge (speak_fluent: sintetiza por partes y reproduce en streaming;
 â†“    barge-in por energÃ­a de mic en llamada/voz)
MEMORIA â†’ add_conversation (episÃ³dica) + append_memory_fact (hechos filtrados)
```

Etapa â†’ componente:

| Etapa | Componente | Archivo |
|-------|-----------|---------|
| INPUT/VAD/turnos | `CallLoop`, `TurnDetector`, `_wake_gate` | `core/call.py` |
| STT | Groq Whisper â†’ fallback faster-whisper | `plugins/stt_whisper/__init__.py` |
| NormalizaciÃ³n | `normalize_user_text`, `fix_transcript` | `core/brain.py`, `plugins/stt_whisper` |
| LLM + routing | `ResilientChatModel`, `build_chat_model`, `reorder_by_usage`, `warmup` | `core/llm.py` |
| Intents/plan | `fast_path_intent`, `fast_first_step`, prompts ReAct | `core/brain.py` |
| TOOLS | 11 plugins (music, ytmusic, discord, browser, pc_control, system, memory, stt, tts, whatsapp + system duplicado en config) | `plugins/*/` |
| RESPONSE/TTS | `speak_fluent`, `ToolNarrator`, `clean_for_speech` | `core/voice.py`, `plugins/tts_edge` |
| Memoria | ChromaDB + SQLite + md | `core/memory.py`, `core/store.py`, `memoria.md` |
| Eventos | `event_bus` (logs, narraciÃ³n, UI futura) | `core/events.py`, `core/main.py` |
| Arranque | animaciÃ³n neuronas, reindex condicional, warmup de backend ganador | `core/boot_anim.py`, `core/main.py` |

## Estado por subsistema (quÃ© funciona)

- ConversaciÃ³n texto/voz/llamada, historial episÃ³dico, RAG sobre vault: funciona.
- MÃºsica personal (YouTube Music con cookie de Opera GX): funciona y verificada
  (15 playlists, 20 likeds, historial; `scripts/sync_mymusic.py`).
- Discord vÃ­a Jockie (`m!*`, vÃ­a silenciosa preferida): funciona.
- Control PC, browser Opera por CDP, system info: implementados, con tests.
- Resiliencia LLM (failover, salud persistente, orden por uso, warmup): funciona.
- Streaming TTS + barge-in por energÃ­a: implementado en llamada/voz.
- Tests: 111 pasando (`pytest tests/`).

## Problemas encontrados (verificados)

1. **Agentes inexistentes**: `agents/` contiene solo `__init__.py`. Los 6 sub-agentes
   de `agents.md` nunca se implementaron; el cerebro los suple con prompts.
2. **Referencia muerta**: `config.yaml` lista `tts_piper`, que no existe como plugin
   (solo `tts_edge`). El registry lo ignora, pero es configuraciÃ³n podrida.
3. **`system` duplicado** en `plugins.enabled` (aparece dos veces).
4. **Cuotas gratuitas al lÃ­mite**: el uso diario (~400k tokens) agota Groq/Gemini y
   deja al sistema en Ollama local. Es el riesgo operativo #1.
5. **STT local pesado**: `faster-whisper large-v3-turbo` en CPU es lento; hoy se
   evita usando Groq, pero sin internet no hay fallback viable rÃ¡pido.
6. **Ruido heredado**: `memoria.md` se habÃ­a contaminado con hechos falsos del
   extractor (ya limpiado 2026-09-16 + filtros anti-ruido en `_extract_facts`).
7. **Modelos razonadores filtran thinking**: mitigado recortando `<think>` y
   relegando OpenRouter fuera de la cadena por defecto.
8. **Sin mÃ©tricas de latencia**: no existe `PERFORMANCE.md`; hay `llm_usage`
   (tokens/llamadas) pero nada de STT/TTS/tool latency (requerido por FASE 15).

## Cuellos de botella (ordenados)

1. Llamadas LLM por turno (4â€“6: understand, planÃ—N, synthesize, extract_facts).
2. Cuotas diarias de tiers gratuitos (429) â†’ degradaciÃ³n a modelos menores.
3. Embeddings vÃ­a Ollama en cada `retrieve` (requiere Ollama corriendo).
4. TTS Edge por red (primer audio depende de internet; sin cachÃ© de frases).
5. Reindex de Chroma en primer arranque (luego incremental por watcher).

## Conservar / reemplazar

- **Conservar**: LangGraph + `brain.py`, `ResilientChatModel`, plugins music/ytmusic/
  discord/stt/tts_edge, `store.py`, `memory.py` (Chroma+SQLite), event bus, toolchain
  de normalizaciÃ³n RAMMA/`m!`, suite de 111 tests.
- **Reparar, no reemplazar**: cadena de fallbacks (ya), `config.yaml` (quitar
  `tts_piper` y duplicado `system`), `agents.md` (difiere de la realidad).
- **Evaluar reemplazo en su fase**: STT local (FASE 3), TTS/piper/XTTS (FASE 9â€“10),
  wake word (FASE 12), sub-agentes reales vs prompts (FASE 4/14), GUI estado (FASE 17).
- **No tocar**: `data/ytmusic_headers.json` (sesiÃ³n vÃ¡lida), `data/claudio.db`,
  `memoria.md` canÃ³nica, `musica.md`.

## DecisiÃ³n FASE 0

AuditorÃ­a completa sin cambios funcionales (solo este documento).
Sistema utterance-to-utterance operativo; base apta para FASE 1.

## BitÃ¡cora de fases

### FASE 1 â€” Base conversacional (2026-09-16, OK)
Verificado contra requisitos: historial episÃ³dico + RAG en prompts,
correcciones ("no, quise decir X" se limpia a X), referencias ("eso/lo de
antes" vÃ­a historial reciente), informal rioplatense, voseo siempre,
espaÃ±ol-only en prompts, intents abrir/cerrar ("cerralo" agregado a pc_close).
Archivos: `core/brain.py`, `plugins/pc_control/__init__.py`, tests Fase 1 en
`tests/test_brain.py` + `tests/test_pc_control.py`. Suite verde.

### FASE 2 â€” Personalidad Iron Man (2026-09-17, OK)
Biblia en `persona.md` (mayordomo preciso/calmo/leal, ironÃ­a seca, economÃ­a
total, voseo, prohibiciones: "por supuesto", "como IA", explicar de mÃ¡s).
Inyectada vÃ­a const `PERSONA` en prompts de chat y sÃ­ntesis (`core/brain.py`).
Respuestas: acciÃ³n simple -> "Listo." / "Enseguida, seÃ±or." Tests Fase 2 en
`tests/test_brain.py`. Suite verde.

### Failover con hedge + router por latencia (track latencia, 2026-09-17, OK)
`ResilientChatModel.ainvoke` ordena backends por salud+latencia EMA y, si el
primero no responde en 2s, prende el siguiente en paralelo (gana el primero).
Medido: primario colgado 30s -> respuesta en 2.03s (antes: 12s timeout + failover).
Hallazgo: `asyncio.wait(timeout)` en ProactorEventLoop/Windows duerme el timeout
completo aunque haya tasks listas -> se usa polling propio (`_wait_done`, 20ms).

### PlanificaciÃ³n compuesta (adelanto de FASE 14, 2026-09-17, OK)
`_parse_compound` convierte "quita y ponÃ© X" / "pausala 5s y reanudala" en cola
ordenada (`control/stop/replay/wait/send_link/search`) que `_plan` ejecuta sin
LLM (tope 7 pasos). Un solo pedido con N acciones se cumple entero. Tests en
`tests/test_brain.py`. Suite verde.

### FASE 3 — STT de alta calidad (2026-09-17, OK)
Auditado: Groq batch (temp 0, es, prompt vocabulario) + fallback local (beam 5, vad_filter, initial_prompt), VAD por energía calibrado, denoise, fin de turno 1.1s. Streaming STT descartado con fundamento: Groq es solo REST y faster-whisper en CPU es muy lento para tiempo real. Agregado: falsos arranques ('pará, no, abrí Chrome' -> 'abrí Chrome', sin tocar negaciones reales 'no pongas X'). Tests en test_brain/test_voice.

### FASE 4 — LLM/cerebro (2026-09-17, OK)
Separación charla/ejecución verificada por auditoría de ruteo (tests con
intents reales). Nuevo: `job_cancel` (mata el proceso, no solo lo marca) +
cancel determinístico ("cancelá/olvidalo", vale con intent unknown) con
confirmación "Cancelado.". Pregunta pendiente con TTL en Store para retomar
el hilo. Correcciones ("mentira, ...") rutean a chat, jamás a discord_send.
Tests en test_brain/test_pc_control. Suite verde.

