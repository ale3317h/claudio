# CLAUDIO - Asistente IA Personal Tipo Iron Man

Asistente personal por voz y texto: **memoria persistente triple** (Obsidian RAG + SQLite + JSON), **cadena de 8 backends LLM gratis** con failover automático, **10 plugins** (Discord, música, PC, navegador, voz...) y **3 modos** (texto, voz, llamada manos libres).

> Funciona hoy, todos los días, en Windows. Ver `CHANGELOG.md` para el historial.

## 🚀 Inicio Rápido

### Prerrequisitos
- Python 3.12+ y Windows 10/11
- Micrófono + parlantes (para voz)
- [Ollama](https://ollama.com/) (opcional, último recurso local):
  ```powershell
  ollama pull llama3.1:8b
  ollama pull nomic-embed-text
  ```

### Instalación
```powershell
cd CLAUDIO_Vault
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env   # completar keys abajo
```

### Keys gratis (sin tarjeta)
| Key | Dónde | Para qué |
|-----|-------|----------|
| `GROQ_API_KEY` | console.groq.com/keys | Cerebro principal (120B, rapidísimo) |
| `GOOGLE_API_KEY` | aistudio.google.com/apikey | 2do cerebro, cupo generoso |
| `OPENROUTER_API_KEY` | openrouter.ai/keys | 3er cerebro (modelos `:free`) |
| `GITHUB_PAT` | GitHub → Settings → Developer settings (permiso Models) | 4to cerebro |
| `DREAMPROMPTING_API_KEY` | panel de DreamPrompting | 5to cerebro (500K/día pooled) |
| `DISCORD_WEBHOOK` | Discord → Server Settings → Integrations → Webhooks | Avisos en Discord |
| `DISCORD_USER_TOKEN` + `DISCORD_CHANNEL_ID` | DevTools → Network → header `authorization` + click derecho al canal | Comandos que los bots obedecen |
| `TAVILY_API_KEY` (+ `TAVILY_API_KEYS`) | tavily.com | Busqueda profunda (1000/mes gratis, rotan) |
| `MODEL_API_KEY` | dev.meta.ai | Muse Spark (pago por uso, opcional) |

### Ejecutar
```powershell
.venv\Scripts\python -m core.main run --text    # chat
.venv\Scripts\python -m core.main run --voice   # push-to-talk
.venv\Scripts\python -m core.main run --call    # llamada manos libres (decí "claudio")
.venv\Scripts\python -m core.main run --text --query "poné tranqui en discord"
```

También hay accesos directos en el escritorio: `CLAUDIO`, `CLAUDIO Voz`, `CLAUDIO Llamada`.

## 🧠 Cómo Funciona

```
voz/texto → wake-word? → intent → memoria (RAG + SQLite) → ReAct 1 paso
→ tools → verifica → replanifica (máx 8) → responde → habla (streaming) → aprende
```

- **Cerebros en cadena**: groq×3 → openrouter → dream → gemini → github → ollama. Salta solos sin cuota (salud en SQLite), con timeouts y fallback.
- **Memoria triple**: `memoria.md` + ChromaDB (semántica) + `data/claudio.db` (datos exactos) + `data/preferences.json`. Extracción automática de hechos.
- **Voz**: STT Groq Whisper (fallback local), TTS Edge neuronal es-AR, VAD con calibración, wake-word Vosk local ("claudio"), barge-in (interrumpilo hablando).

## 🔌 Plugins (10)

| Plugin | Función |
|--------|---------|
| `stt_whisper` | Voz a texto (Groq, fallback local) |
| `tts_edge` | Texto a voz neuronal (Edge, es-MX Jorgeás) |
| `pc_control` | Apps, ventanas, tipeo, hotkeys, shell, jobs en 2do plano |
| `discord` | Webhook + comandos como usuario (Jockie obedece) |
| `music` | Buscar en YouTube, anti-repetidos, guarda artistas |
| `ytmusic` | Tu cuenta YouTube Music (playlists, historial) |
| `browser` | Opera por CDP (abrir, pestañas, leer con tu sesión) |
| `whatsapp` | Enviar por WhatsApp Desktop |
| `memory` | Datos exactos (`recordá...`), preferencias |
| `system` | Hora/fecha/CPU/RAM/disco al instante |

## 📁 Estructura

```
CLAUDIO_Vault/            # ← ESTO ES un vault de Obsidian (abrilo ahí)
├── CLAUDIO.md            # Portada navegable del asistente
├── agents.md            # Especificación técnica (biblia del proyecto)
├── core/                # brain, memory, llm (cadena), voice, call, store
├── plugins/             # 10 plugins (ver tabla)
├── tests/               # 270+ tests (pytest)
├── config.yaml          # Modelos, voz, permisos
├── memoria.md / usuario.md / musica.md   # Memoria legible
└── data/                # ChromaDB, SQLite, perfiles (gitignored)
```

## 🛡️ Notas honestas

- Los tiers gratis tienen cuotas: la cadena rota sola, y Ollama local cubre siempre (lento).
- Self-bot de Discord y multi-cuentas Groq violan ToS de esas plataformas (riesgo de baneo, decisión tuya).
- Free tiers de Google pueden usarse para entrenar sus productos.

## 📚 Más contexto

- `agents.md` — especificación original completa
- `SUPER_PROMPT_CLAUDIO.md` — prompt para desarrollar con IA
- `CHANGELOG.md` — historial de versiones
- `CLAUDIO.md` — dashboard del asistente

---
*"
## Voz (mic, tecla, pitido)

- Tecla push-to-talk en `config.yaml` -> `voice.ptt_key`: `numlock`, `rctrl` (sin bips), `pause`, `scroll`, `insert` o codigo hex.
- Si una tecla no responde: `scripts/probe_ptt.py` muestra cuales detecta tu teclado.
- Si Bloq Num pita: `scripts/togglekeys_off.py` apaga el bip.
- Cada turno muestra `(audio: Xs, pico -NdBFS)`: MIC MUY BAJO = habla mas cerca o subi ganancia. Queda en `data/last_mic.wav`.
- Wake-word Vosk (`vosk-model-small-es-0.42` en `data/vosk-model/`): https://alphacephei.com/vosk/models

## YouTube Music (tu cuenta)

1. Credenciales OAuth (TVs and Limited Input) en Google Cloud.
2. `scripts/setup_ytmusic_oauth.py` (una sola vez).
3. Sync: `scripts/sync_youtube_official.py`.

## Trabajar con otro OpenCode encima

- Lee `agents.md` + este README primero.
- Tests: `.venv/Scripts/python -m pytest tests/ -q` (verde siempre).
- Regex de STT/musica duplicados en `core/brain.py`, `plugins/music`, `plugins/ytmusic`, `plugins/stt_whisper` (tocar los 4).
- Personaliza `usuario.md` + `memoria.md`. Tus artistas van en `_KNOWN_ARTISTS` + prompt STT + tests.

## Problemas comunes

- Transcribe cualquier cosa: mic en silencio, Voicemod cerrado o mal dispositivo (ver `data/last_mic.wav`).
- Backends CAIDO: keys muertas o cuota; se reintentan solos al arrancar.
- Cuota Tavily: 2da key en `TAVILY_API_KEYS` o DuckDuckGo automatico.


A veces hay que correr antes de poder caminar."* — Tony Stark
