# 🤖 CLAUDIO

> Asistente personal de [[usuario|Señor]]. Corre en tu PC, piensa con IA en la nube (gratis) y recuerda todo en este vault.

## Hablarle

| Cómo | Comando / acceso directo |
|------|--------------------------|
| Llamada manos libres | `CLAUDIO Llamada` (escritorio) o `python -m core.main run --call` |
| Voz push-to-talk | `CLAUDIO Voz` o `python -m core.main run --voice` |
| Texto | `CLAUDIO` o `python -m core.main run --text` |

Decile **"claudio"** antes del pedido en modo llamada. Para música en Discord: *"poné / pausá / reanudá / saltá"*.

## Memoria

- [[memoria]] — hechos duraderos (auto-gestionado, editable)
- [[usuario]] — quién sos
- [[musica]] — comandos de Jockie + playlist [[#Tranqui|Tranqui]]
- `data/claudio.db` — datos exactos y preferencias (técnico)
- `daily-notes/` — bitácora diaria (próximamente automática)

## Capacidades

- 🗣️ Voz en tiempo real (escucha, piensa en voz alta, lo podés interrumpir)
- 🎵 Música: busca links, evita repetidos, guarda cantantes
- 💬 Discord: mensajes, comandos a bots (Jockie obedece)
- 🖥️ PC: abrir apps, ventanas, tipeo, tareas en segundo plano
- 🌐 Opera con tu sesión + WhatsApp Desktop
- 🧠 Recuerda gustos, artistas y hechos entre sesiones

## Motor (técnico)

- `core/brain.py` — orquestador ReAct (el cerebro posta)
- `agents.md` — especificación completa del sistema
- `config.yaml` — modelos, voz, permisos
- Plugins en `plugins/` (discord, música, pc, voz, memoria...)

---
*Generado por CLAUDIO · se actualiza con cada versión*
