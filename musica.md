# Música en Discord (Jockie)

Comandos como usuario (el bot SOLO obedece mensajes humanos, ignora webhooks):
- Reproducir: `m!play <link de YouTube>`
- Pausar: `m!pause`
- Reanudar: `m!resume`
- Saltar tema: `m!skip`
- Ver cola: `m!queue`
- Frenar y limpiar: `m!stop`

Reglas para CLAUDIO:
- En 2do plano / silencioso: `discord_user_send` (los bots obedecen, no roba foco).
- Tipeo UI (`discord_command`) solo si lo piden explícito o falla la vía silenciosa.
- Comandos SIEMPRE con M: `m!play`/`m!p`, `m!pause`, `m!resume`, `m!skip`, `m!queue`, `m!stop`. PROHIBIDO `!play`/`!pause` sin la M.
- Controles (pausa/reanuda/salta/cola/stop/para/alto) van DIRECTO al comando: PROHIBIDO buscarlos como si fueran temas. "Pausa" solo se busca si hablan del TEMA ("poné el tema Pausa").
- FUENTE PRINCIPAL: tu cuenta de YouTube (OAuth permanente en `data/ytmusic_oauth.json`). Orden: 1) link directo -> `m!p`, 2) `mymusic_cached_play` (cache local con tus playlists + likeds, instantáneo), 3) `mymusic_search` puntual. `music_search` SOLO para mostrar opciones.
- "LA/MI playlist X" (ej "poné la playlist pop") = TU biblioteca: link completo a Discord. "BUSCÁ/UNA playlist de X" (ej "buscá una playlist de Milo J", sin MI) = AFUERA (search_playlists, públicas). Sin especificar dónde = SIEMPRE Discord.
- Opera GX (plugin `browser`) NO se usa para leer tu biblioteca (para eso está la API); solo para abrir/mostrar `music.youtube.com` si lo piden. No abras el Opera real con otro perfil mientras lo usás vos (se bloquea el perfil).
- Si la API dice "sesión falló": NO pidas F12 manual. Decí que corra `.venv\Scripts\python scripts\setup_ytmusic_oauth.py` (fix definitivo, una sola vez) o `scripts\refresh_cookie_from_opera.py` con Opera CERRADO como parche. Mientras tanto usa tu cache local (`mymusic_cached_play`) + búsqueda pública.
- ARTISTA RAMMA: se escribe SIEMPRE con doble M (RAMMA). Si oís "rama", es RAMMA. En `mymusic_search`/`music_play` usa `query="RAMMA ..."` y `artist="RAMMA"`. Si piden RAMMA sin tema, query=`RAMMA` general (no asumir "Algún día").
- "Algún día" solo si lo piden explícito. "Esa música"/"esa" vago = último artista del historial (normalmente RAMMA).
- Dictado "r, b, r" / "rb" = álbum RV de RAMMA.
- Tras mandar UNA playlist (link con list=): `m!select random` y despues `m!remove after played`, una vez cada uno. Solo playlists, nunca temas sueltos. Si frena despues, nada.
- Sync de gustos: `.venv\Scripts\python scripts\sync_youtube_official.py` (API oficial, permanente) guarda `data/mymusic_cache.json`. Se refresca solo al arrancar si tiene +24h.
