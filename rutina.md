# Rutina diaria de CLAUDIO (tareas que se repiten)

> REGLA GitHub: al cerrar cada tanda, commit + push SOLO de código/docs/tests.
> Secretos (.env, cookies, claudio.db, claves) quedan SIEMPRE en esta PC, ya
> ignorados en .gitignore. Nunca leerlos en chat ni subirlos.

Lo de todos los días, ya a mano (sin pedir ni cargar nada):

## Música en Discord (lo principal)
1. Buscar en TU cuenta primero: cache local (`data/mymusic_cache.json`, instantáneo)
   -> `mymusic_cached_play` (tranqui / likeds / artista) o `mymusic_search` (tema puntual).
2. Solo si tu cuenta falla: YouTube genérico (`music_play`).
3. Ni bien hay link: `m!p <link>` a Discord en 2do plano, AL INSTANTE (automático).
4. Si piden cambiar: `m!stop` primero, después el tema nuevo.
5. Nunca repetir: salta lo ya sonado. Comandos siempre con M (`m!pause`, `m!resume`, `m!skip`).

## Tus datos guardados
- Playlists y likeds: `data/mymusic_cache.json` (se refresca solo al arrancar si tiene +24h,
  o manual con `.venv\Scripts\python scripts\sync_mymusic.py`).
- Playlist Tranqui + reglas Jockie: [[musica]].
- Gustos (RAMMA, Paulo Londra, Airbag...): [[memoria]].

## Programas
- Discord (servidor + canal de música, bot Jockie con `m!`).
- Opera GX (navegador + sesión de YouTube Music de donde sale la cookie).
- Obsidian (este vault = memoria).

## Órdenes de corte (interrumpir YA)
- "claudio para" / "para" / "alto" / "basta": frena la voz al instante y manda `m!stop`.
