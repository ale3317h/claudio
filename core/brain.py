import asyncio
import json
import re
import time
from typing import Any, Dict, List, Literal, Optional, TypedDict

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, StateGraph
from pydantic import BaseModel

from core.config import settings
from core.events import emit_event
from core.llm import build_chat_model
from core.memory import MemorySystem
from plugins.registry import PluginRegistry

logger = structlog.get_logger(__name__)

MAX_PLAN_RETRIES = 2  # 3 reintentos = hasta 8 llamadas lentas; 2 alcanza
MAX_TOTAL_STEPS = 7  # compuestas (stop+buscar+mandar+espera+reanudar) entran justo

# Atajo sin LLM para control de musica/comandos directos.
# Ahorra 2 llamadas al LLM (~2-6s) en los pedidos mas comunes.
_FAST_MUSIC_RE = re.compile(
    r"\b(pau[sz]\w*|reanud\w*|resume|contin\w*|sigue|salt\w*|skip\w*|skipe[ao]\w*|esquip[ei][ao]?\w*|siguiente|otra(?:\s+(?:canci[oó]n|tema|de))?|pon\w*|reproduc\w*|pasame|busc[áa]\w*|mostr\w*|decime|fren[aeá]\w*|par[ae]\w*|det[eé]n\w*|silenci\w*)",
    re.IGNORECASE,
)
_FAST_CMD_RE = re.compile(r"m!(play|p|pause|resume|skip|queue|stop)\b", re.IGNORECASE)


_LINK_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
PERSONA = (
    "Sos CLAUDIO como en Iron Man, en rioplatense: mayordomo preciso, calmo y leal, "
    "ironía seca y sobria, jamás ansioso ni servil. Hablás poco y exacto. "
    'PROHIBIDO: "por supuesto", "procederé", "con gusto", "estoy para servirle", '
    '"como IA/modelo de lenguaje", explicar de más, repreguntar lo obvio. '
    'Acción simple -> "Listo." / "Enseguida, señor." / "Sí, un segundo."'
)
_ORDERS_CACHE = {"mtime": 0.0, "text": ""}


def load_orders(max_chars: int = 1800) -> str:
    """La biblia (ordenes.md) para inyectar en prompts. Cacheada por mtime."""
    try:
        from core.config import settings
        p = settings.memory.vault_path / "ordenes.md"
        mtime = p.stat().st_mtime
        if mtime != _ORDERS_CACHE["mtime"]:
            _ORDERS_CACHE["mtime"] = mtime
            _ORDERS_CACHE["text"] = p.read_text(encoding="utf-8")
        return (_ORDERS_CACHE["text"] or "")[:max_chars]
    except Exception:
        return ""


_RESET_RE = re.compile(
    r"\b(empez[ao][a-z]*\s+de\s+cero|limpi[ao][a-z]*\s+el\s+contexto|"
    r"olvida[a-z]*\s+lo\s+anterior|cambiemos\s+de\s+tema|nuevo\s+tema)\b",
    re.IGNORECASE,
)


def wants_reset(text: str) -> bool:
    """¿Pidió arrancar la conversación de cero? Hilo nuevo, sin arrastre."""
    return bool(_RESET_RE.search(text or ""))


_MEMCMD_FWD_RE = re.compile(
    r"\b(guarda|guardá|guárdalo|guardalo|anota|anotá|recordá|acordate|"
    r"no te olvides|anotalo|anotálo)"
    r"(\s+eso)?(\s+en tu memoria)?[,:]?\s+(.+)",
    re.IGNORECASE,
)
_MEMCMD_BWD_RE = re.compile(
    r"([^.!?…]+?)[.!?…]\s*(eso\s+)?(guárdalo|guardalo|anotalo|anotálo|recordalo|recordálo)"
    r"(\s+en tu memoria)?",
    re.IGNORECASE,
)
_MEMCMD_NOW_RE = re.compile(
    r"\ba partir de ahora[,:]?\s+(.+)",
    re.IGNORECASE,
)


def _clean_fact(core: str) -> str:
    core = re.sub(r"^(?:[A-Za-z]\.\s*)+", "", (core or "").strip(" ,.!?¿¡:;"))
    return core.strip(" ,.!?¿¡:;")


def extract_memory_commands(text: str) -> list:
    """Órdenes explícitas de memoria ('guardalo en tu memoria', 'acordate que...').

    Devuelve los hechos tal cual los dictó, para guardarlos literales.
    El comando puede ir antes ("guardá que me gusta X") o después del dato
    ("X. Eso guardalo en tu memoria").
    """
    facts = []
    t = " ".join((text or "").split())
    if not t:
        return facts
    for m in _MEMCMD_BWD_RE.finditer(t):
        core = _clean_fact(m.group(1))
        if len(core) > 10 and core not in facts:
            facts.append(core)
    for m in _MEMCMD_FWD_RE.finditer(t):
        core = _clean_fact(m.group(4))
        if re.match(r"(?:[A-Za-z]\.\s*|y\s+si\b)", core, re.IGNORECASE):
            continue  # resto de frase ajena ("C. Y si, ..."), no un dato
        if len(core) > 10 and core not in facts:
            facts.append(core)
    for m in _MEMCMD_NOW_RE.finditer(t):
        core = _clean_fact(m.group(1))
        if len(core) > 10 and core not in facts:
            facts.append(core)
    return facts
# "de vuelta / de nuevo / otra vez / repetí" = LA ANTERIOR, no un tema nuevo.
# (Ojo: existe un tema llamado "La Vuelta": por eso jamás se busca esto.)
_REPLAY_RE = re.compile(
    r"\b(de\s+[vb]ue?lt[ao]s?|de\s+nuevo|otra\s+vez|repeti[a-z]*|"
    r"la\s+misma|el\s+mismo)\b",
    re.IGNORECASE,
)
# Tema famoso -> dueño (para no traer homónimos de otros artistas).
_KNOWN_SONGS = (("nublado", "Paulo Londra"), ("en una", "RAMMA"),
                ("adan y eva", "Paulo Londra"), ("bichiyal", "RAMMA"),
                ("gonna fly now", "Bill Conti"), ("eye of the tiger", "Survivor"),
                ("otro beso", "RAMMA"), ("nostalgia", "RAMMA"),
                ("algun dia", "RAMMA"), ("algún día", "RAMMA"),
                ("cuando pase la tormenta", "RAMMA"))
# "You wanna split now / gonna flight now" (STT) = "Gonna Fly Now" (Rocky).
_GONNA_FLY_RE = re.compile(
    r"\b(gonna|gona|wana|wanna|yana)\s+(fly|flight|split|flay)\s+now\b",
    re.IGNORECASE,
)
# "Rocky/Racky" pelado = el TEMA de Rocky (Gonna Fly Now), no la pelicula.
# El STT lo escucha con K ("Racky"). Se mapea directo al tema para que el
# anclaje de Bill Conti lo reconozca.
_ROCKY_RE = re.compile(r"\brocky\b|\bracky\b|\braky\b|\broky\b|\broki\b|\brucky\b"
                       r"|\brackynolds\b|\brockynolds\b",
                       re.IGNORECASE)
# "Oro beso" (STT) = "Otro beso" (RAMMA).
_OTRO_BESO_RE = re.compile(r"\boro\s+beso\b", re.IGNORECASE)
_FILLER_RE = re.compile(
    r"\b(em+e*|eh+|este+|che|capo|loco|bro|amigo|hermano|genio|claudio|por\s*favor|"
    r"porfa|dale|poneme|pon[eé]?|reproduc[eií]\w*|repruc[eií]\w*|pasame|busc[aoá]\w*|"
    r"eleg[eií]\w*|una\s+por\s*m[ií]|pod[eé]s|puedes|podr[íi]as|quiero|quer[ée]s|"
    r"quit[aoá]\w*|sac[aoá]\w*|saqu[aeé]\w*|coloc[aoá]\w*|cambi[aoá]\w*|pas[aoá]\w*|"
    r"cort[aoá]\w*|fren[aoá]\w*|deten\w*|par[áa]|ese|esa|esos|esas|este|esta|"
    r"esto|anterior|actual|misma|mismo|otro|otra|nuevo|nueva|"
    r"no\s+hace\s+falta|no\s+importa|olvida[a-z]*|eeeh+|eee+h+|mmm+|uuuh*|traen?|"
    r"quise\s+decir|\bdigo\b|me\s+refier[ao]|me\s+equivoqu[ée]|perd[oó]n|"
    r"hijo\s+de\s+(puta|mil)|hijo\s+de\s+mil\s+putas?|la\s+concha(\s+de\s+.*)?|"
    r"\bputa\b|\bputo\b|\bmierda\b|\bcarajo\b|\bboludo\b|\bpelotudo\b|"
    r"\bidiota\b|\bimb[eé]cil\b|\best[uú]pido\b|\btarado\b|\bforro\b|"
    r"\bin[uú]til\b|\bmaldit[oa]\b|\bchoto\b|\bchota\b|\bverga\b|\bporonga\b|"
    r"\bpija\b|\bsorete\b|\bgil\b|\bgiles\b|\bnabo\b|\bmog[oó]lico\b|"
    r"o\s+sea|osea|(del|al|en\s+el)\s+disco|(del|al|en\s+el)\s+discord|te\s+deletreo|deletre[ao][a-z]*|"
    r"de\s+[vb]ue?lt[ao]s?|de\s+nuevo|otra\s+vez|repeti[a-z]*|"
    r"la\s+misma|el\s+mismo|"
    r"que\s+no\s+sea\s+[a-záéíóúñ]+|menos\s+[a-záéíóúñ]+|"
    r",?\s*\bno\s+(?:pongas|ponga|pongan|pongo|reproduzcas|reproduzca|suene|suena)\s+(?:la\s+de\s+|el\s+de\s+|la\s+|el\s+)?[a-záéíóúñ]+(?:\s+[a-záéíóúñ]+){0,2}|"
    r",?\s*\bno\s+(?:la\s+de\s+|el\s+de\s+|la\s+|el\s+)?[a-záéíóúñ]{4,}|"
    r"alguna?\s*m[uú]sica|canci[oó]n|tema|video|link|enlace|en\s+discord|en\s+spotify|spotify|"
    r"en\s+2\s*do\s+plano|en\s+segundo\s+plano|de\s+fondo|sin\s+interrumpir)\b",
    re.IGNORECASE,
)


# Limpieza de exclusiones de version para BUSCAR ("pero no en vivo" no se
# manda a YouTube: sin operador NOT, esos tokens rankean justo lo prohibido).
# La exclusion se aplica al ELEGIR (_first_link/excluded_words), no al buscar.
_VERSION_STRIP_RE = re.compile(
    r"\bpero\s+no\s+(?:en\s+vivo|ac[úu]stico|en\s+directo|remix|cover|live|en\s+espa[ñn]ol|espa[ñn]ol)\b"
    r"|\b(?:no|sin)\s+(?:en\s+vivo|ac[úu]stico|en\s+directo|remix|cover|live)\b"
    r"|\bque\s+no\s+sea\s+(?:en\s+vivo|ac[úu]stico|en\s+directo|remix|cover|live|[a-záéíóúñ]{3,}(?:\s+[a-záéíóúñ]{2,}){0,2})\b",
    re.IGNORECASE,
)


def music_query_from_text(text: str) -> str:
    """Limpia muletillas del pedido y deja tema/artista. Si no queda nada util, RAMMA."""
    t = normalize_user_text(text or "")
    t = _GONNA_FLY_RE.sub("Gonna Fly Now", t)
    t = _ROCKY_RE.sub("Gonna Fly Now", t)
    t = _OTRO_BESO_RE.sub("Otro beso", t)
    t = _LINK_RE.sub(" ", t)
    # Titulos conocidos que el filler rompería ("Otro" es muletilla pero
    # "Otro beso" es tema): se fijan antes y se restauran después.
    low0 = t.lower()
    pinned = [ti for ti, _a in _KNOWN_SONGS if len(ti) > 6 and ti in low0]
    t = _FILLER_RE.sub(" ", t)
    t = _VERSION_STRIP_RE.sub(" ", t)
    t = re.sub(r"\bm[áa]s\s+de\b", " ", t, flags=re.IGNORECASE)  # "más de X" = otra vez X
    t = " ".join(t.split()).strip(" ,.!?¿¡\"'")
    t = re.sub(r"^(de|del)\s+", "", t, flags=re.IGNORECASE).strip()
    # "Música (de)" inicial no es parte del nombre del tema.
    t = re.sub(r"^((el|la|los|las|un|una)\s+)?m[uú]sica(\s+de)?(\s+|$)",
               "", t, flags=re.IGNORECASE).strip()
    # Basura inicial del STT ("y, ...", "eh, ..."): conjuncion/interjeccion suelta.
    # Va DESPUES del strip de musica porque ese puede exponer un "y " nuevo
    # ("la música y X" -> "y X" -> "X").
    t = re.sub(r"^([,;.!?¿¡\s]+|(y|e|eh|che)\b[,\s]+)+", "", t, flags=re.IGNORECASE).strip()
    if not t:
        return "RAMMA"
    low = t.lower()
    for ti in pinned:
        if ti not in low:
            t = f"{t} {ti}".strip()
            low = t.lower()
    # Temas famosos sin artista ("Nublado" = Paulo Londra): se agrega el dueño
    # para no traer cualquier cosa con el mismo nombre.
    low = t.lower()
    for _title, _art in _KNOWN_SONGS:
        if _title in low and _art.lower() not in low:
            t = f"{t} {_art}".strip()
            break
    # Basura tipo "el y" (restos de "busca el link y"): sin palabra util -> RAMMA.
    if not any(len(w) > 2 for w in t.split()):
        return "RAMMA"
    return t if len(t) >= 3 else "RAMMA"


def fast_first_step(text: str, intent_name: str):
    """Primer paso deterministico (0 tokens) para musica. None si no aplica.

    Garantiza que un pedido de musica SIEMPRE dispare una herramienta,
    aunque el LLM de planificacion falle o devuelva vacio.
    """
    if intent_name not in ("music_search", "discord_command", "mymusic"):
        return None
    t = normalize_user_text(text or "")
    up0 = t.upper()
    if ("RAMMA" not in up0 and "PAULO LONDRA" not in up0 and "SKILLET" not in up0
            and _BAND_REF_RE.search(t)):
        a = recall_artist()
        if a:
            t = f"{t} {a}"
    cmd = control_command(t)
    if cmd is not None:
        # Control exacto: va DIRECTO al que este sonando, jamás se busca.
        # music_control mira el sistema (Discord/Opera/Spotify); m!queue es solo Jockie.
        if cmd == "m!queue":
            return ActionStep(tool="discord_user_send", args={"message": cmd},
                              depends_on=[])
        action = {"m!pause": "pause", "m!resume": "resume",
                  "m!skip": "skip", "m!stop": "stop"}[cmd]
        return ActionStep(tool="music_control", args={"action": action},
                          depends_on=[])
    if _REPLAY_RE.search(t) and not _LINK_RE.search(t):
        # Solo es replay si NO nombra tema/artista puntual: resto genérico
        # ("la música", nada) -> la anterior. Con tema puntual, flujo normal.
        bare = " ".join(_FILLER_RE.sub(" ", _REPLAY_RE.sub(" ", t)).split())
        words = [w.lower() for w in re.findall(r"[A-Za-zÁ-ÿ]+", bare) if len(w) > 2]
        generic = {"musica", "música", "cancion", "canción", "canciones", "tema",
                   "temas", "video", "videos", "discord", "esa", "ese", "eso",
                   "esta", "este", "esto"}
        bu = bare.upper()
        known = any(ti in bare.lower() for ti, _a in _KNOWN_SONGS)
        if ("RAMMA" not in bu and "PAULO LONDRA" not in bu and not known
                and all(w in generic for w in words)):
            return ActionStep(tool="music_replay", args={}, depends_on=[])
    if (_STOP_NOW_RE.search(t) and not _PLAY_VERBS_RE.search(t)
            and not _LINK_RE.search(t)):
        # Quitar/limpiar/borrar SIN tema nuevo: frenar directo (m!stop limpia
        # la cola en Jockie). Con tema nuevo se busca primero y el stop sale solo.
        # "otra"/"siguiente" solas NO frenan: piden tema nuevo.
        return ActionStep(tool="music_control", args={"action": "stop"},
                          depends_on=[])
    m = _LINK_RE.search(t)
    if m and ("musi" in t.lower() or "pon" in t.lower() or "reproduc" in t.lower()
              or "m!" in t or "tema" in t.lower() or "canci" in t.lower()):
        return ActionStep(tool="discord_user_send",
                          args={"message": f"m!p {m.group(0)}"}, depends_on=[])
    return _search_step_for(t)


def _resolve_misma(text: str) -> str:
    """'esa misma musica' -> ultimo tema que sono ('caracara RAMMA')."""
    if not re.search(r"\b(misma|mismo)\b", text or "", re.IGNORECASE):
        return text or ""
    try:
        import json as _json

        from core.config import settings
        from core.store import Store
        store = Store(settings.memory.vault_path / "data")
        for event in store.recent_events(kind="musica", limit=10):
            try:
                data = _json.loads(event.get("content", ""))
            except Exception:  # noqa: S112 - eventos viejos corruptos se saltean
                continue
            if isinstance(data, dict) and data.get("url"):
                fill = f"{data.get('title', '')} {data.get('artists', '')}".strip()
                if not fill:
                    return text or ""
                return re.sub(r"esa\s+misma(\s+m[u\u00fasica]+)?|la\s+misma|el\s+mismo",
                              fill, text, flags=re.IGNORECASE)
        return text or ""
    except Exception:
        return text or ""


def _playlist_name(t: str) -> str:
    """Nombre tras 'playlist (de)' o 'pelis': 'poné la playlist pop' -> 'pop'."""
    low = (t or "").lower()
    m = re.search(r"\b(?:playlists?|pelis?)\s+(?:de\s+)?(.+)$", low)
    rest = (m.group(1) if m else low).strip()
    rest = rest.strip(" ,.!?¿¡;:\"'").strip()
    rest = re.sub(r"^(mi|mis|m[ií]a|m[ií]as|la|el|las|los|una?)\s+", "", rest).strip()
    rest = re.sub(r"\s+(en\s+)?(discord|liscord|youtube|yt|music)\s*$", "", rest).strip()
    # Resto vacio o preposicion suelta ("en", "de") = sin nombre.
    if len(rest) < 2 or rest in ("en", "de", "la", "el", "y", "e", "con", "por",
                                 "para", "mi", "mis", "del", "al", "en el", "de la"):
        return ""
    return rest


def _wants_outside_playlists(t: str) -> bool:
    """'buscá una playlist de Milo J' = afuera. 'la/mi playlist X' = mía."""
    low = (t or "").lower()
    if "playlist" not in low:
        return False
    if re.search(r"\b(mi|mis|m[ií]a|m[ií]as)\b", low) and "biblioteca" in low or \
            re.search(r"\bmi\s+playlists?\b|\bla\s+playlists?\b", low):
        return False
    if "tranqui" in low or "biblioteca" in low:
        return False
    if re.search(r"\b(busc[áa]|buscando|buscame|encontr[áa]|rastre[áa])\b", low):
        return True
    return bool(re.search(r"\buna\s+playlists?\s+de\b", low))


def _search_step_for(t):
    """Rama de búsqueda de fast_first_step (tranqui/cache/cuenta/red). Idéntica."""
    low = t.lower()
    if re.search(r"\b[áa]lbum(es)?\b", low):
        # "poné el álbum Homerun / un álbum de Paulo Londra" = ALBUM completo,
        # no un tema. El tool devuelve su playlist y el encadenado la manda.
        return ActionStep(tool="mymusic_album", args={"query": t}, depends_on=[])
    if "playlist" in low and _wants_outside_playlists(t):
        # "buscá/una playlist de Milo J" = playlists PÚBLICAS de afuera,
        # NUNCA tu biblioteca. Con verbo de poner, el encadenado la manda a Discord.
        q = _playlist_name(t)
        if q:
            return ActionStep(tool="search_playlists", args={"query": q},
                              depends_on=[])
    if "tranqui" in low or "playlist" in low or "peli" in low:
        # "LA/MI playlist pop" / "mis pelis" = TU biblioteca (link a Discord).
        # "No lo hagas / solo decime" GANA sobre todo: listar, jamas mandar.
        # Pregunta ("cuáles son", "decime", "lista") SIN verbo de poner =
        # LISTAR (mymusic_playlists), JAMAS mandar m!p. Solo con verbo de poner
        # se manda la playlist completa. "Un tema de..." = un tema, no la lista.
        if _NO_PLAY_RE.search(t):
            return ActionStep(tool="mymusic_playlists", args={"limit": "25"},
                              depends_on=[])
        if not re.search(r"\bun\s+tema\b|\buna\s+canci[oó]n\b|\balg[úu]n\s+tema\b", low):
            if _SHOW_ONLY_RE.search(t) and not _PLAY_VERBS_RE.search(t):
                return ActionStep(tool="mymusic_playlists", args={"limit": "25"},
                                  depends_on=[])
            try:
                from core.mymusic_cache import find_playlist
                name = _playlist_name(t)
                if "tranqui" in low:
                    hit = find_playlist("tranqui")
                else:
                    hit = find_playlist(name) if name else None
                    if hit is None:
                        hit = find_playlist(t)
                if hit:
                    return ActionStep(
                        tool="discord_user_send",
                        args={"message": f"m!p {hit['url']}"}, depends_on=[])
                if "playlist" in low or "peli" in low:
                    # Pidió una playlist sin nombre ubicable: mostrarlas,
                    # NO adivinar un tema cualquiera.
                    return ActionStep(tool="mymusic_playlists", args={"limit": "25"},
                                      depends_on=[])
            except Exception:
                pass
        return ActionStep(tool="mymusic_cached_play", args={"source": "tranqui"},
                          depends_on=[])
    if any(k in low for k in ("me gusta", "algo mio", "algo mío", "mis gustos",
                              "mi biblioteca", "biblioteca", "liked", "likeds")):
        return ActionStep(tool="mymusic_cached_play", args={"source": "liked"},
                          depends_on=[])
    if any(k in low for k in ("volver a escuchar", "lo de antes", "lo que escuchaba",
                               "segui con eso", "seguí con eso", "eso de nuevo",
                               "lo ultimo que sono", "lo último que sonó")):
        return ActionStep(tool="mymusic_recent", args={"limit": "10"}, depends_on=[])
    if any(k in low for k in ("favoritos olvidados", "algo viejo", "hace rato no escucho",
                               "hace mucho no escucho")):
        return ActionStep(tool="mymusic_forgotten", args={"limit": "10"}, depends_on=[])
    if re.search(r"\blista\b", low) and re.search(
            r"\b(mis|música|musica|playlist|playlists|canciones|temas)\b", low):
        # "mostrame la lista (de mis...)" = listar TU biblioteca, no buscar.
        return ActionStep(tool="mymusic_playlists", args={"limit": "25"},
                          depends_on=[])
    if any(k in low for k in ("historial", "ultimo", "último", "venia escuchando",
                              "venía escuchando", "que escucho", "que mas escucho",
                              "que más escucho", "top", "favorit", "mis temas")):
        return ActionStep(tool="mymusic_history", args={"limit": "15"}, depends_on=[])
    q = music_query_from_text(t)
    up = q.upper()
    artist = "RAMMA" if "RAMMA" in up else ("Paulo Londra" if "PAULO LONDRA" in up else "")
    if artist and up.replace(artist.upper(), "").strip(" ,-") == "":
        # Solo artista sin tema ("otra", "elegi una por mi"): primero tu cache
        # (instantaneo, salta repetidos); si esta vacio el planner usa la red.
        return ActionStep(tool="mymusic_cached_play",
                          args={"source": "", "artist": artist}, depends_on=[])
    # Cuenta primero (conoce tus gustos y desambigua titulos pelados como "en una").
    # music_play generico queda como fallback del planner si tu cuenta falla.
    return ActionStep(tool="mymusic_search", args={"query": q}, depends_on=[])


_PLAY_VERBS_RE = re.compile(
    r"\b(pon[eé](lo|la|los|las|me)?|poner|reproduc\w*|repruc[eií]\w*|pas[áa]|mand[áa]|son[áa]|escuch\w*|o[íi]\w*|oye\w*|play|m!|dale|coloc[aoá]|agreg[aoá]|sum[aoá]|met[eé])\b",
    re.IGNORECASE,
)
_DELAY_RE = re.compile(
    r"\b(despues|después)\s+de\s+(\d+)\s+segundos?\b"
    r"|\b(espera|esperá|aguanta|aguantá)[a-z]*\s+(\d+)\s+segundos?\b"
    r"|\ben\s+(\d+)\s+segundos?\b"
    r"|\b(pausa|pausá|frena|frená|deten|espera)\w*\s+(\d+)\s+segundos?\b",
    re.IGNORECASE,
)


def delay_seconds(text: str) -> int:
    """¿Pidió esperar N segundos antes de seguir? 0 si no. Tope 300s."""
    m = _DELAY_RE.search(text or "")
    if not m:
        return 0
    for g in m.groups():
        if g and g.isdigit():
            return max(1, min(300, int(g)))
    return 0


_NUM_WORDS = {"cero": 0, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4,
               "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
               "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
               "dieciseis": 16, "dieciséis": 16, "diecisiete": 17, "dieciocho": 18,
               "diecinueve": 19, "veinte": 20, "treinta": 30, "cuarenta": 40,
               "cincuenta": 50, "sesenta": 60, "setenta": 70, "ochenta": 80,
               "noventa": 90, "cien": 100, "ciento": 100}
_OP_WORDS = (("dividido por", "/"), ("dividido", "/"), ("entre", "/"), ("por", "*"),
             ("más", "+"), ("mas", "+"), ("menos", "-"), ("menos", "-"),
             ("más", "+"), ("modulo", "%"), ("módulo", "%"), ("resto", "%"),
             ("potencia", "**"), ("elevado", "**"))


def try_calc_answer(text: str):
    """'¿Cuánto es 5 más 5?' -> '25'. Cuentas exactas sin LLM. None si no es cuenta."""
    import ast
    t = " ".join((text or "").lower().split())
    m = re.search(r"(cu[aá]nto es|calcula|calculá|resolv[eé]|cuanto da)\s+(.+?)[?.!]*$", t)
    if not m:
        return None
    expr = m.group(2)
    for word, val in sorted(_NUM_WORDS.items(), key=lambda e: -len(e[0])):
        expr = re.sub(rf"\b{word}\b", str(val), expr)
    for word, op in _OP_WORDS:
        expr = expr.replace(word, f" {op} ")
    expr = re.sub(r"[^0-9+\-*/%(). ]", " ", expr)
    expr = " ".join(expr.split())
    if not expr or not re.search(r"\d", expr) or not re.search(r"[+\-*/%]", expr):
        return None
    try:
        node = ast.parse(expr, mode="eval")
    except Exception:
        return None
    allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Add,
               ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow, ast.USub, ast.UAdd,
               ast.FloorDiv)
    try:
        for n in ast.walk(node):
            if not isinstance(n, allowed) or (isinstance(n, ast.Constant)
                                              and not isinstance(n.value, (int, float))):
                return None
            if isinstance(n, ast.Pow) and isinstance(getattr(n, "right", None), ast.Constant) \
                    and abs(n.right.value) > 10:
                return None
        val = eval(compile(node, "<calc>", "eval"), {"__builtins__": {}}, {})  # noqa: S307
    except Exception:
        return None
    if isinstance(val, float):
        if abs(val) > 1e12 or val != val:
            return None
        val = round(val, 2)
        if float(val).is_integer():
            val = int(val)
    return str(val)


_IDENTITY_RE = re.compile(
    r"soy un modelo de lenguaje|como modelo de lenguaje|inteligencia artificial,?\s+(cread[oa]|diseñad[oa])|"
    r"\bAI language model\b|como IA\b",
    re.IGNORECASE,
)


def scrub_response(text: str) -> str:
    """Saca oraciones prohibidas (identidad corporativa, YouTuber) de una respuesta."""
    parts = re.split(r"(?<=[.!?…])\s+", (text or "").strip())
    kept = [p for p in parts if p.strip() and not _IDENTITY_RE.search(p)
            and not _YT_SPAM_RE.search(p)]
    out = " ".join(kept).strip()
    return out if out else "Sí, señor."


def _skel(s: str) -> str:
    """Esqueleto consonántico para comparar ('Resistence' == 'Resistance')."""
    import unicodedata
    t = "".join(c for c in unicodedata.normalize("NFD", (s or "").lower())
                if unicodedata.category(c) != "Mn")
    return re.sub(r"[^bcdfghjklmnpqrstvwxyz]", "", t)


def excluded_words(text: str) -> list:
    """Frases a EVITAR ('no pongas Nublado', 'no la de Resistence', 'menos Y').

    Captura la CANCIÓN (no el verbo): 'no pongas Nublado' -> 'Nublado'.
    Multi-palabra ('No Puedo') se compara por esqueleto consonántico.
    """
    out = []
    t = text or ""
    patterns = (
        r"\bno\s+(?:pongas|ponga|pongan|pongo|reproduzcas|reproduzca|suene|suena)\s+"
        r"(?:la\s+de\s+|el\s+de\s+|la\s+|el\s+)?([A-Za-zÁ-ÿ]{2,}(?:\s+[A-Za-zÁ-ÿ]{2,}){0,2})",
        r"\bno\s+(?:la\s+de\s+|el\s+de\s+|la\s+|el\s+)?([A-Za-zÁ-ÿ]{4,}(?:\s+[A-Za-zÁ-ÿ]{2,}){0,2})",
        r"\bque\s+no\s+sea\s+([A-Za-zÁ-ÿ]{3,}(?:\s+[A-Za-zÁ-ÿ]{2,}){0,2})",
        r"\bmenos\s+([A-Za-zÁ-ÿ]{4,}(?:\s+[A-Za-zÁ-ÿ]{2,}){0,2})",
    )
    _VERBS = {"pongas", "ponga", "pongan", "pongo", "pone", "ponen", "reproduce",
              "reproduci", "reproducir", "suena", "suene", "suenan", "pasa", "pasan"}
    if re.search(r"\bno\s+en\s+espa[ñn]ol\b", t, re.IGNORECASE):
        out += ["español", "spanish"]
    # Versiones a evitar ("no/sin en vivo/acústico/remix"): se comparan por
    # esqueleto en el titulo ("En Vivo" -> se salta).
    m2 = re.search(r"\b(?:no|sin)\s+(en\s+vivo|ac[úu]stico|en\s+directo|remix|cover|live)\b",
                   t, re.IGNORECASE)
    if m2:
        out.append(" ".join(m2.group(1).split()))
    for rx in patterns:
        for m in re.finditer(rx, t, re.IGNORECASE):
            w = " ".join(m.group(1).split())
            if len(_skel(w)) >= 3 and w.lower() not in [x.lower() for x in out]:
                # No capturar verbos sueltos ("no pongas" sin canción detrás).
                if w.split()[0].lower() in _VERBS:
                    continue
                out.append(w)
    return out


def _blocked_title(title: str, query: str = "") -> bool:
    """Temas vetados salvo pedido explícito (el fantasma de 'Algún día')."""
    sk = _skel(title)
    if "lgnd" in sk:  # algún día / algun dia
        return "lgnd" not in _skel(query)
    return False


def _title_excluded(title: str, excluded) -> bool:
    sk = _skel(title)
    return any(len(_skel(w)) >= 3 and _skel(w) in sk for w in (excluded or []))


# Artistas canonicos con sus variantes de STT: si el pedido nombra uno, el
# link elegido debe ser DE ese artista (no el primer resultado cualquiera).
_KNOWN_ARTISTS = (
    ("RAMMA", re.compile(r"\bramm\w*|\bram+a\b", re.IGNORECASE)),
    ("Paulo Londra", None),  # usa _PAULO_RE (esta mas abajo en el modulo)
    ("Saja Boys", re.compile(r"\b(saga|aga|saja)\s+boys?\b|\bsaja\s+b\b", re.IGNORECASE)),
    ("Skillet", None),  # usa _SKILLET_RE
    ("Bill Conti", re.compile(r"\bbill\s+conti\b|\bgonna\s+fly\s+now\b", re.IGNORECASE)),
    ("Survivor", re.compile(r"\bsurvivor\b|\beye\s+of\s+the\s+tiger\b", re.IGNORECASE)),
    ("WOS", re.compile(r"\bwos\b", re.IGNORECASE)),
    ("Airbag", re.compile(r"\bairbag\b", re.IGNORECASE)),
)


def _query_artist(query: str) -> str:
    """Artista canonico nombrado en el pedido, o ''."""
    q = _GONNA_FLY_RE.sub("Gonna Fly Now", query or "")
    for name, rx in _KNOWN_ARTISTS:
        if rx is None:
            if name == "Paulo Londra" and _PAULO_RE.search(q):
                return name
            if name == "Skillet" and _SKILLET_RE.search(q):
                return name
            continue
        if rx.search(q):
            return name
    return ""


def _artist_match(artist: str, title: str, artists: str) -> bool:
    if not artist:
        return False
    hay = _skel(f"{artists} {title}")
    a = _skel(artist)
    return bool(a) and (a in hay or all(w in hay for w in a.split()))


_CHAT_CACHE_TTL = 600.0  # respuestas repetidas ("me escuchas?") salen sin LLM
_CHAT_NOCACHE_RE = re.compile(
    r"hora|fecha|hoy|ayer|mañana|manana|ahora|noticias|noticia|clima|tiempo|"
    r"cu[aá]nto|qui[eé]n|d[oó]nde|cu[aá]ndo|por qu[eé]|precio|dolar|dólar|"
    r"partido|resultado|edad|significa|que es\b",
    re.IGNORECASE,
)


def _chat_cacheable(text: str) -> bool:
    t = (text or "").strip()
    return 3 <= len(t) <= 60 and not _CHAT_NOCACHE_RE.search(t)


_CANCEL_RE = re.compile(
    r"\b(cancel[aeo][a-z]*|olvidal[ao][a-z]*|olv[ií]date|olvida\s+eso|"
    r"d[eé]jalo|deja\s+eso|corta\s+todo|para\s+todo|no\s+sigas)\b",
    re.IGNORECASE,
)
# OJO: "olvidados" (favoritos olvidados) NO es cancelar: por eso es
# olvidal+lo/la (olvidalo/olvidala), no olvid[ao][a-z]* que matcheaba todo.


def _save_pending_question(text: str) -> None:
    """Guarda lo que se le preguntó para retomar en el turno siguiente."""
    try:
        import json as _json
        import time as _t

        from core.config import settings
        from core.store import Store
        Store(settings.memory.vault_path / "data").remember(
            "pregunta_pendiente", _json.dumps({"q": text[:300], "ts": _t.time()}))
    except Exception:
        pass


def _load_pending_question(max_age_s: float = 600.0):
    """Pregunta pendiente fresca o None (TTL 10 min, se pisa sola)."""
    try:
        import json as _json
        import time as _t

        from core.config import settings
        from core.store import Store
        raw = Store(settings.memory.vault_path / "data").recall("pregunta_pendiente")
        if not raw:
            return None
        data = _json.loads(raw)
        if _t.time() - float(data.get("ts", 0)) > max_age_s:
            return None
        return (data.get("q") or "").strip() or None
    except Exception:
        return None


def _cancel_step(text: str, tools):
    """'No, cancelá' / 'olvidalo' -> mata jobs en curso. Sin LLM."""
    if "job_cancel" not in (tools or {}):
        return None
    if _CANCEL_RE.search(text or ""):
        return ActionStep(tool="job_cancel", args={}, depends_on=[])
    return None


def _has_pending_action(text: str) -> bool:
    """¿El pedido trae OTRA acción además de la ya hecha? ('quita y poné X').

    Si sí, NO se da por terminado: hay que seguir (buscar y mandar el tema).
    """
    t = text or ""
    if _LINK_RE.search(t):
        return True
    return bool(_PLAY_VERBS_RE.search(t))
_SHOW_ONLY_RE = re.compile(
    r"\b(mostr|enseñ|enseñame|qu[eé]\s+hay|opciones|lista|decime|cuales|cu[aá]les)\b",
    re.IGNORECASE,
)
_SWAP_RE = re.compile(
    r"\b(quit[aoá]\w*|sac[aoá]\w*|saqu[aeé]\w*|limpi[aoá]\w*|elimin[aoá]\w*|borr[aoá]\w*|"
    r"cort[aoá]\w*|fren[aoá]\w*|deten\w*|cambi[aoá]\w*|"
    r"par[áa](?!\s+que\b)|siguiente|otra|clear|tala)\b",
    re.IGNORECASE,
)
# Correccion del usuario hacia CLAUDIO ("mentira, no lo hiciste"): NUNCA es
# contenido para mandar a Discord ni accion: es charla. Solo vale si no trae
# orden de envio ni pedido (ver _is_correction).
_CORRECTION_RE = re.compile(
    r"\bmentira\b|\bno\s+lo\s+hiciste\b|\bno\s+hiciste\b|\bte\s+equivocaste\b"
    r"|\best[áa]\s+mal\b|\beso\s+no\b|\bnada\s+que\s+ver\b|\bno\s+es\s+eso\b"
    r"|\bme\s+entendiste\s+mal\b",
    re.IGNORECASE,
)
_SEND_VERBS_RE = re.compile(
    r"\b(mand[áa]\w*|envi[aoá]\w*|escrib\w*|public[aoá]\w*|decile?s?|decime|contale?s?|avisale?s?)\b",
    re.IGNORECASE,
)


def _is_correction(text: str) -> bool:
    """¿Es un reto/correccion a CLAUDIO? Entonces es charla, jamas discord_send."""
    t = text or ""
    if not _CORRECTION_RE.search(t):
        return False
    if _SEND_VERBS_RE.search(t) or _PLAY_VERBS_RE.search(t) or _LINK_RE.search(t):
        return False
    return not re.search(r"(?i)^m!\w+", t.strip())


# Negacion explicita de reproduccion ("no lo pongas", "solo decime"): gana
# sobre cualquier verbo de poner. Mostrar, jamas mandar m!play.
_NO_PLAY_RE = re.compile(
    r"\bno\s+(?:lo|la|las|los\s+)?hagas\b"
    r"|\bno\s+(?:lo|la|las|los\s+)?pongas\b"
    r"|\btodav[ií]a\s+no\b|\ba[uú]n\s+no\b"
    r"|\bsolo\s+(decime|mostrame|contame|dec[ií])\b",
    re.IGNORECASE,
)
# Verbos destructivos solos (sin tema nuevo): frenar YA, sin buscar.
# "otra"/"siguiente" NO entran: piden tema nuevo. "para la tarea" tampoco.
_STOP_NOW_RE = re.compile(
    r"\b(paralo|parala|paralos|paralas|parate|par[áa](?!\s+(que|la\s+tarea)\b)|"
    r"alto|basta(\s+ya)?|corta(lo|la)?|deten\w*|frena\w*|"
    r"quit[aoá]\w*|sac[aoá]\w*|saqu[aeé]\w*|limpi[aoá]\w*|elimin[aoá]\w*|"
    r"borr[aoá]\w*|cort[aoá]\w*|cambi[aoá]\w*|clear|stop|tala)\b",
    re.IGNORECASE,
)


def _sent_commands(state):
    return list((state.get("metadata") or {}).get("sent_commands") or [])


def _playlist_followup(state):
    """Tras mandar UNA playlist (m!p con list=): m!select random y despues
    m!remove after played (comandos reales de Jockie). Una vez cada uno por
    turno; si freno (m!stop) despues, nada. Temas sueltos: nada."""
    sent = _sent_commands(state)
    pl_idx = -1
    for i, c in enumerate(sent):
        if re.match(r"(?i)^m!(p|play)\b", (c or "").strip()) and "list=" in (c or ""):
            pl_idx = i
    if pl_idx == -1:
        return None
    after = sent[pl_idx + 1:]
    if any(re.match(r"(?i)^m!stop\b", (c or "").strip()) for c in after):
        return None
    if not any(re.match(r"(?i)^m!select\s+random\b", (c or "").strip()) for c in after):
        return ActionStep(tool="discord_user_send",
                          args={"message": "m!select random"}, depends_on=[])
    if not any(re.match(r"(?i)^m!remove\s+after\s+played\b", (c or "").strip()) for c in after):
        return ActionStep(tool="discord_user_send",
                          args={"message": "m!remove after played"}, depends_on=[])
    return None


def _play_sent(state):
    return any(re.match(r"(?i)^m!(p|play)\b", (c or "").strip()) for c in _sent_commands(state))


def _stop_sent(state):
    return any(re.match(r"(?i)^m!stop\b", (c or "").strip()) for c in _sent_commands(state))


def _is_english_version(title: str) -> bool:
    t = (title or "").lower()
    return "english" in t or "en inglés" in t or "en ingles" in t


def _first_link(results, query: str = "", artist: str = ""):
    """Primer link reproducible, saltando vetados y excluidos ('no la de X').

    Si el pedido nombra un artista (RAMMA, Saja Boys...), primero se busca un
    link DE ese artista; si ninguno, se usa el primero válido (fallback viejo).
    Con 'en inglés' se prefiere versión English y se salta '(Spanish)'.
    """
    excluded = excluded_words(query)
    artist = artist or _query_artist(query)
    want_en = bool(re.search(r"ingl[eé]s|english", query or "", re.IGNORECASE))
    if want_en:
        excluded = list(excluded) + ["español", "spanish"]
    want_official = bool(re.search(r"\boficial\b|official", query or "", re.IGNORECASE))
    playlist_fallback = ""
    anchored = []
    general = []
    for k in _ordered_step_keys(results or {}):
        r = (results or {}).get(k)
        if not isinstance(r, dict) or r.get("ok") is not True:
            continue
        cands = []
        pick = r.get("pick")
        if isinstance(pick, dict) and isinstance(pick.get("url"), str):
            cands.append(pick)
        for it in (r.get("results") or [])[:5] + (r.get("tracks") or [])[:5]:
            if isinstance(it, dict) and isinstance(it.get("url"), str):
                cands.append(it)
        for c in cands:
            u = (c.get("url") or "").strip()
            # ID de video real (no vacio): 'watch?v=' pelado es basura de STT/API.
            if not re.search(r"watch\?v=[\w-]+|youtu\.be/[\w-]+", u):
                if "list=" in u and not playlist_fallback:
                    playlist_fallback = u
                continue
            if "watch?v=" in u or "youtu.be/" in u:
                title = str(c.get("title", ""))
                if _blocked_title(title, query):
                    continue
                if _title_excluded(title, excluded):
                    continue
                if artist and _artist_match(artist, title, str(c.get("artists", ""))):
                    anchored.append((u, title))
                else:
                    general.append((u, title))
                continue
            # Links de playlist (Jockie las reproduce): solo si no hay tema.
            if not playlist_fallback and "list=" in u:
                playlist_fallback = u
    for pool in (anchored, general):
        if not pool:
            continue
        if want_official:
            for u, title in pool:
                if "official" in (title or "").lower():
                    return u
        if want_en:
            for u, title in pool:
                if _is_english_version(title):
                    return u
        return pool[0][0]
    # Si todo estaba vetado/excluido NO se devuelve nada: mandar igual el
    # prohibido es peor que no mandar. El que llama presenta opciones o avisa.
    return playlist_fallback


def _parse_compound(t, intent_name):
    """Pedido con 2+ acciones -> [(pos, kind, val)] en orden de mención.

    kind: control(m!cmd) | stop | replay | wait(N) | send_link(url) | search.
    [] si hay 0-1 acciones (ahi manda la via rapida simple de siempre).
    """
    if intent_name not in ("music_search", "discord_command", "mymusic"):
        return []
    items = []
    song_mode = _means_song(t)
    for rx, cmd in _CONTROL_MAP:
        for m in rx.finditer(t):
            if cmd == "m!pause" and song_mode:
                continue  # hablan del TEMA Pausa, no de pausar
            items.append((m.start(), "control", cmd))
    if _SIGUE_RE.search(t) and "?" not in t:
        m = _SIGUE_RE.search(t)
        items.append((m.start(), "control", "m!resume"))
    for m in _SWAP_RE.finditer(t):
        w = m.group(0).lower()
        if w in ("otra", "siguiente"):
            continue  # "otra" sola pide tema nuevo, no frenar
        items.append((m.start(), "control", "m!stop"))
    if _REPLAY_RE.search(t) and not _LINK_RE.search(t):
        bare = " ".join(_FILLER_RE.sub(" ", _REPLAY_RE.sub(" ", t)).split())
        words = [w.lower() for w in re.findall(r"[A-Za-zÁ-ÿ]+", bare) if len(w) > 2]
        generic = {"musica", "música", "cancion", "canción", "canciones", "tema",
                   "temas", "video", "videos", "discord", "esa", "ese", "eso",
                   "esta", "este", "esto"}
        bu = bare.upper()
        known = any(ti in bare.lower() for ti, _a in _KNOWN_SONGS)
        if ("RAMMA" not in bu and "PAULO LONDRA" not in bu and "SKILLET" not in bu
                and not known and all(w in generic for w in words)):
            m = _REPLAY_RE.search(t)
            items.append((m.start(), "replay", None))
    for m in _DELAY_RE.finditer(t):
        for g in m.groups():
            if g and g.isdigit():
                items.append((m.start(), "wait", str(max(1, min(300, int(g))))))
                break
    for m in _LINK_RE.finditer(t):
        if ("musi" in t.lower() or "pon" in t.lower() or "reproduc" in t.lower()
                or "m!" in t or "tema" in t.lower() or "canci" in t.lower()):
            items.append((m.start(), "send_link", m.group(0)))
    if (_PLAY_VERBS_RE.search(t) or "tranqui" in t.lower()
            or any(k in t.lower() for k in ("me gusta", "biblioteca", "liked", "likeds",
                                            "historial", "top", "mis temas"))
            or "RAMMA" in t.upper() or "PAULO LONDRA" in t.upper()
            or "SKILLET" in t.upper()):
        m = _PLAY_VERBS_RE.search(t)
        items.append((m.start() if m else len(t), "search", None))
    items.sort(key=lambda e: e[0])
    out = []
    for pos, kind, val in items:
        if not out or out[-1][1:] != (kind, val):
            out.append((pos, kind, val))
    # "Pero ANTES/PRIMERO ...": los stop van antes del buscar/mandar,
    # aunque se mencionen después en la frase.
    if re.search(r"\b(antes|primero)\b", t, re.IGNORECASE):
        stops = [e for e in out if e[1] == "control" and e[2] == "m!stop"]
        if stops:
            rest = [e for e in out if not (e[1] == "control" and e[2] == "m!stop")]
            idx = next((i for i, e in enumerate(rest) if e[1] in ("search", "send_link")),
                       len(rest))
            out = rest[:idx] + stops + rest[idx:]
    # "Busca X + reproducilo": primero se averigua QUÉ es X (una llamada chica),
    # después se busca y se manda. Sin esto se busca la frase literal y falla.
    if _KNOW_RE.search(t):
        idx = next((i for i, e in enumerate(out) if e[1] == "search"), None)
        if idx is not None:
            out.insert(idx, (out[idx][0], "resolve", None))
    if len(out) <= 1:
        return []
    return [(kind, val) for _, kind, val in out]


_KNOW_RE = re.compile(
    r"m[aá]s\s+(reproducid[oa]|escuchad[oa]|famos[oa]|vist[oa])\b"
    r"|\btop\b|\b[ée]xito\b|\bhit\b|\bmejor\s+tema\b|\bmas\s+pegad[oa]\b",
    re.IGNORECASE,
)


def _build_seq_item(item, state, t):
    """Construye el ActionStep de un item de la cola. None si no se puede ahora."""
    kind, val = item
    if kind == "search":
        rq = (state.get("metadata") or {}).get("resolved_query") or ""
        if rq:
            return ActionStep(tool="mymusic_search", args={"query": rq},
                              depends_on=[])
        return _search_step_for(t)
    if kind == "control":
        if val == "m!queue":
            return ActionStep(tool="discord_user_send", args={"message": val},
                              depends_on=[])
        action = {"m!pause": "pause", "m!resume": "resume",
                  "m!skip": "skip", "m!stop": "stop"}[val]
        return ActionStep(tool="music_control", args={"action": action},
                          depends_on=[])
    if kind == "replay":
        return ActionStep(tool="music_replay", args={}, depends_on=[])
    if kind == "wait":
        return ActionStep(tool="music_wait", args={"segundos": str(val)},
                          depends_on=[])
    if kind == "send_link":
        return ActionStep(tool="discord_user_send",
                          args={"message": f"m!p {val}"}, depends_on=[])
    if kind == "search":
        return _search_step_for(t)
    return None


def auto_music_step(state):
    """Encadena música sin LLM: link encontrado -> m!p al instante (0 tokens).

    - Si pidieron quitar/cambiar y aún no se frenó: m!stop primero.
    - Si hay link y no se mandó play: m!p <link> directo a Discord.
    - Si solo quieren VER opciones (mostrame/qué hay, sin verbo de poner):
      no se manda nada, decide el LLM.
    """
    intent = state.get("intent")
    if intent is None or getattr(intent, "name", "") not in (
            "music_search", "discord_command", "mymusic"):
        return None
    if not _ordered_step_keys(state.get("results") or {}):
        return None
    text = state.get("user_input") or ""
    if _SWAP_RE.search(text) and not _stop_sent(state):
        return ActionStep(tool="music_control", args={"action": "stop"},
                          depends_on=[])
    # Playlist recien mandada (m!p con list=): modo aleatorio + limpiar
    # tocados, una vez cada uno. Solo playlists, nunca temas sueltos.
    # Si freno despues (m!stop), no se configura nada.
    follow = _playlist_followup(state)
    if follow is not None:
        return follow
    if _play_sent(state):
        return None
    # Espera pedida ("después de 5 segundos..."): se cumple antes de seguir.
    hist = list((state.get("metadata") or {}).get("tool_history") or [])
    if delay_seconds(text) and "music_wait" not in hist:
        return ActionStep(tool="music_wait",
                          args={"segundos": str(delay_seconds(text))},
                          depends_on=[])
    # Red de seguridad: si una busqueda anterior se contamino con un control
    # ("pausar la musica..." buscado como tema), se ejecuta el control directo
    # en vez de mandar el link basura.
    rescue = _rescue_control(state)
    if rescue is not None:
        return rescue
    if _SHOW_ONLY_RE.search(text) and not _PLAY_VERBS_RE.search(text):
        return None
    # Negacion explicita ("no lo pongas", "solo decime"): mostrar, jamas mandar.
    if _NO_PLAY_RE.search(text or ""):
        return None
    # Gate anti-random: sin señal de poner (verbo/link/flujo de play), NO se
    # auto-manda nada. Una búsqueda sucia ("que tala música...") no termina
    # sonando: se presenta o se pide aclaración.
    if not _wants_play(text, hist):
        # Excepcion: aclaracion que nombra UNA playlist ("la playlist de
        # Villeros, con doble L") sin pregunta ni verbo de buscar: se manda.
        # "Buscá/mostrame" y "?" siguen mostrando sin mandar.
        # PERO con nombre puntual debe EXISTIR en lo listado: si piden "pop"
        # y solo hay like/Villeros, no se manda la primera (fue el bug del "like").
        if _wants_playlist_link(text):
            name = _playlist_name(text)
            pl = ""
            if not name or _playlist_name_in_results(name, state.get("results") or {}):
                pl = _first_playlist(state.get("results") or {}, name)[1]
            if pl:
                return ActionStep(tool="discord_user_send",
                                  args={"message": f"m!p {pl}"}, depends_on=[])
        return None
    # "Reproducí ALGUNA de mis playlists" (verbo poner + playlists, sin nombre):
    # se manda la primera en vez de solo listar. Con nombre puntual manda el
    # flujo con nombre (find_playlist); preguntas de solo-ver ya salieron arriba.
    if (not _play_sent(state)
            and _PLAY_VERBS_RE.search(text)
            and not _NO_PLAY_RE.search(text or "")
            and re.search(r"\bplaylists?\b", text, re.IGNORECASE)
            and _playlist_unnamed(text)):
        pl_title, pl_url = _first_playlist(state.get("results") or {})
        if pl_url:
            return ActionStep(tool="discord_user_send",
                              args={"message": f"m!p {pl_url}"}, depends_on=[])
    link = _first_link(state.get("results") or {}, text)
    if not link:
        return None
    return ActionStep(tool="discord_user_send", args={"message": f"m!p {link}"},
                      depends_on=[])


def _wants_play(text, hist) -> bool:
    """¿El usuario quiere ESCUCHAR (no solo ver)? Sin esto, no hay auto-play."""
    t = text or ""
    if _PLAY_VERBS_RE.search(t) or _LINK_RE.search(t):
        return True
    return any(h in ("music_play", "mymusic_cached_play") for h in (hist or []))


# Verbos de BUSCAR/mostrar: con estos se presenta, no se manda solo.
_SEARCH_ONLY_RE = re.compile(
    r"\b(busc[áa]|buscando|buscame|mostr[aoá]?|enseñ[aoá]?|encontr[áa])\b",
    re.IGNORECASE,
)


def _wants_playlist_link(text: str) -> bool:
    """¿Aclaracion que nombra una playlist para PONER? ('la playlist de
    Villeros'). Excluye preguntas, negaciones y verbos de buscar/mostrar."""
    t = text or ""
    low = t.lower()
    if "playlist" not in low and "peli" not in low:
        return False
    if "?" in t or _SHOW_ONLY_RE.search(t) or _NO_PLAY_RE.search(t):
        return False
    return not _SEARCH_ONLY_RE.search(t)


def _playlist_name_in_results(name: str, results) -> bool:
    """¿El nombre pedido aparece en las playlists listadas?

    Freno anti-'like': si piden 'pop' y solo hay like/Villeros/Ponk,
    NO se manda la primera (seria poner CUALQUIER cosa).
    Coincidencia parcial a proposito ("Villeros, con doble L" vale).
    """
    nt = [w for w in re.findall(r"[a-záéíóúñ]+", (name or "").lower()) if len(w) > 2]
    if not nt:
        return True  # sin nombre extraible: comportamiento viejo
    for k in _ordered_step_keys(results or {}):
        r = (results or {}).get(k)
        if not isinstance(r, dict) or r.get("ok") is not True:
            continue
        for it in (r.get("playlists") or []) + (r.get("results") or []):
            if not isinstance(it, dict):
                continue
            title = str(it.get("title", "") or "").lower()
            if title and any(w in title for w in nt):
                return True
    return False


def _first_playlist(results, name: str = "") -> tuple:
    """(titulo, link) de playlist (list=) en resultados ok.

    Con nombre: la primera que coincida (no la primera a secas: fue el bug
    del "like" cuando pidieron "pop"). Sin nombre: la primera.
    """
    nt = [w for w in re.findall(r"[a-záéíóúñ]+", (name or "").lower()) if len(w) > 2]
    first = None
    for k in _ordered_step_keys(results or {}):
        r = (results or {}).get(k)
        if not isinstance(r, dict) or r.get("ok") is not True:
            continue
        for it in (r.get("playlists") or [])[:5] + (r.get("results") or [])[:3]:
            if isinstance(it, dict):
                u = (it.get("url") or "").strip()
                if "list=" in u:
                    title = str(it.get("title", "") or "").strip()
                    if first is None:
                        first = (title, u)
                    if nt and title and any(w in title.lower() for w in nt):
                        return (title, u)
    return first or ("", "")


def _playlist_link(results) -> str:
    """Primer link de playlist (list=) en resultados ok, o ''."""
    return _first_playlist(results)[1]


def _playlist_unnamed(text: str) -> bool:
    """'alguna de mis playlists' / 'mis playlists' = sin nombre puntual.

    _playlist_name trae el resto crudo ("alguna de mis..."); si solo quedan
    genericas, no hay nombre y vale la primera.
    """
    rest = _playlist_name(text or "")
    if not rest:
        return True
    generic = {"alguna", "alguno", "algunas", "algunos", "una", "uno", "unas",
               "unos", "mis", "mi", "mia", "mias", "cualquiera", "cualquier",
               "algo", "otra", "otro", "esa", "ese", "este", "esta", "de", "en",
               "el", "la", "las", "los", "del", "al", "y", "e", "con", "por",
               "para", "que", "tus", "tu",                "playlist", "playlists", "peli", "pelis",
               "cuales", "cuáles", "cual", "cuál", "qué", "que", "quien", "quién",
               "donde", "dónde",
               "cuando", "cuándo", "como", "cómo", "cuanto", "cuánto", "son", "es",
               "hay", "tengo", "tenes", "tenés", "cualesson"}
    words = [w for w in re.findall(r"[a-záéíóúñ]+", rest.lower()) if w not in generic]
    return not words


def _last_search_query(state) -> str:
    meta = state.get("metadata") or {}
    hist = list(meta.get("tool_history") or [])
    keys = _ordered_step_keys(state.get("results") or {})
    for i in range(len(keys) - 1, -1, -1):
        if i < len(hist) and hist[i] in ("mymusic_search", "music_search", "music_play",
                                             "search_playlists"):
            r = (state.get("results") or {}).get(keys[i])
            if isinstance(r, dict):
                q = r.get("query")
                if isinstance(q, str) and q.strip():
                    return q
    return ""


def _sent_has(state, cmd: str) -> bool:
    pat = re.compile(rf"(?i)^{re.escape(cmd)}\b")
    return any(pat.search((c or "").strip()) for c in _sent_commands(state))


def _rescue_control(state):
    """Si lo ultimo buscado fue en realidad un control, ejecutarlo directo."""
    q = _last_search_query(state)
    if not q:
        return None
    cmd = control_command(q)
    if cmd is None or _sent_has(state, cmd):
        return None
    # Si el usuario hablaba de un TEMA llamado asi, no rescatar nada.
    if _means_song(state.get("user_input") or ""):
        return None
    if cmd == "m!queue":
        return ActionStep(tool="discord_user_send", args={"message": cmd}, depends_on=[])
    action = {"m!pause": "pause", "m!resume": "resume",
              "m!skip": "skip", "m!stop": "stop"}[cmd]
    return ActionStep(tool="music_control", args={"action": action}, depends_on=[])


def play_already_done(state):
    """El m!play ya salió con ok:true: no hace falta ni plan ni LLM, directo a done."""
    if _play_sent(state):
        keys = _ordered_step_keys(state.get("results") or {})
        return bool(keys) and isinstance((state.get("results") or {})[keys[-1]], dict) \
            and (state.get("results") or {})[keys[-1]].get("ok") is True
    return False


_CONTROL_SENT_RE = re.compile(
    r"(?i)^m!(pause|resume|unpause|skip|queue|stop|clear)\b")


def deterministic_done(state):
    """Una acción determinística ya salió con ok:true: terminar SIN más LLM.

    Cubre m!play (play_already_done) y controles (music_control o m! directo).
    Sin esto, despues de cada ok se quemaban 2 llamadas LLM (plan + respuesta)
    solo para decir 'Pausado' — con cuotas muertas son 24s de espera boba.
    """
    if play_already_done(state):
        return True
    keys = _ordered_step_keys(state.get("results") or {})
    if not keys:
        return False
    last = (state.get("results") or {}).get(keys[-1])
    if not isinstance(last, dict) or last.get("ok") is not True:
        return False
    hist = list((state.get("metadata") or {}).get("tool_history") or [])
    last_tool = hist[len(keys) - 1] if len(keys) - 1 < len(hist) else ""
    done = False
    if last_tool == "music_control":
        done = True
    if last_tool in ("discord_user_send", "discord_command"):
        done = any(_CONTROL_SENT_RE.search((c or "").strip())
                   for c in _sent_commands(state))
    if done and _has_pending_action(state.get("user_input") or ""):
        return False  # ej: "quita y poné X": falta la segunda parte
    return done


_CONTROL_MAP = [
    (re.compile(r"\bpau[sz][aoá]\w*\b", re.IGNORECASE), "m!pause"),
    (re.compile(r"\b(reanud[aoá]\w*|renov[aoá]\w*|renuev[aoá]\w*|resume|despaus[aoá]\w*|contin[uú][aoá]\w*)\b",
                re.IGNORECASE), "m!resume"),
    (re.compile(r"\bsalt[aoá]\w*\b|\bresalt[aoá]\w*\b|\bskip\w*|\bskipe[ao]\w*|\besquipe[aoá]\w*|\besquipi[aoá]\w*|\b[ae]?\s*quip[ie]\w*\b|\bsiguiente\b", re.IGNORECASE), "m!skip"),
    (re.compile(r"\bcola\b|\bqueue\b", re.IGNORECASE), "m!queue"),
    (re.compile(r"\b(paralo|parala|paralos|paralas|parate|par[áa](?!\s+(que|la\s+tarea)\b)|"
                r"alto|basta(\s+ya)?|corta(lo|la)?|deten\w*|frena\w*|stop|tala|"
                r"limpi[aoá]\w*)\b(?!\s+que\b)",
                re.IGNORECASE), "m!stop"),
]
_SIGUE_RE = re.compile(r"\bsigue\b|\bsiga\b|\bsegu[íi]\b", re.IGNORECASE)


def _means_song(text: str) -> bool:
    """¿Hablan de un TEMA llamado Pausa/Pega...? ('poné el tema Pausa', '"Pausa" de Vicka').

    En ese caso NO es orden de control: hay que buscarlo, no pausar.
    """
    t = text or ""
    return bool(_PLAY_VERBS_RE.search(t) and re.search(
        r'["«»]|se llama|el tema|la canci[oó]n|titulad', t, re.IGNORECASE))


def control_command(text: str):
    """Orden de control exacta (pausa/reanuda/salta/cola/stop) o None.

    Prioridad máxima: un control NUNCA se busca como música. '¿sigue sonando?'
    es pregunta, no orden. Un tema que se llame Pausa no es orden (ver _means_song).
    """
    t = " ".join((text or "").split())
    if not t or _means_song(t):
        return None
    if _SIGUE_RE.search(t) and "?" in t:
        return None
    for rx, cmd in _CONTROL_MAP:
        if rx.search(t):
            return cmd
    if _SIGUE_RE.search(t):
        return "m!resume"
    return None


def _is_pure_time_question(text: str) -> bool:
    """¿Pregunta SOLO hora/fecha local? ('qué hora es', 'decime la fecha').
    Con lugares u otra cosa ('en Tokio') va al LLM."""
    words = re.findall(r"[A-Za-zÁ-ÿñü]+", (text or "").lower())
    ok = {"que", "qué", "q", "hora", "es", "son", "la", "las", "el",
          "fecha", "dia", "día", "hoy", "decime", "dime", "me", "decis",
          "tenes", "tienes", "podes", "puedes", "cual", "cuál"}
    words = [w for w in words if w not in ("claudio",)]
    return bool(words) and all(w in ok for w in words)


def _local_time_answer(user_input: str, results) -> str:
    """'Son las 00:59.' / 'Hoy es ...' sin LLM. '' si no aplica."""
    if not _is_pure_time_question(user_input):
        return ""
    data = None
    for k in _ordered_step_keys(results or {}):
        r = (results or {}).get(k)
        if isinstance(r, dict) and r.get("ok") is True and ("hora" in r or "fecha" in r):
            data = r
            break
    if not data:
        return ""
    low = (user_input or "").lower()
    parts = []
    if re.search(r"\bhora\b", low) and data.get("hora"):
        parts.append(f"Son las {data['hora']}.")
    if re.search(r"\b(fecha|d[ií]a|hoy)\b", low) and data.get("fecha"):
        parts.append(f"Hoy es {data['fecha']}.")
    if not parts and data.get("hora"):
        parts.append(f"Son las {data['hora']}.")
    return " ".join(parts).strip()


def _system_time_step(intent_name: str, tools):
    """Hora/fecha local sin LLM. None si no aplica."""
    if intent_name == "system_time" and tools and "system_time" in tools:
        return ActionStep(tool="system_time", args={}, depends_on=[])
    return None


def _has_known_music(text: str) -> bool:
    """El texto nombra musica conocida: artista canonico, tema canonico o
    cancion famosa de _KNOWN_SONGS. Para rutear a musica sin LLM."""
    t = (text or "").lower()
    if _query_artist(t):
        return True
    tq = _GONNA_FLY_RE.sub("Gonna Fly Now", _ROCKY_RE.sub("Gonna Fly Now", t)).lower()
    if "gonna fly now" in tq or "eye of the tiger" in tq:
        return True
    # "en una" (6) se excluye: aparece en frases comunes ("pensa en una cosa").
    return any(len(ti) > 6 and ti in t for ti, _a in _KNOWN_SONGS)


# Planner: por intent solo se muestran las tools que puede necesitar.
# 42 tools con params = ~2k tokens por llamada; podar a ~13 ahorra ~2/3.
# Fallback seguro: intent desconocido o pedido con browser = TODAS.
_PLANNER_TOOL_KEEP = {
    "music_search": ("music", "mymusic", "discord", "job_cancel"),
    "discord_command": ("music", "mymusic", "discord", "job_cancel"),
    "mymusic": ("music", "mymusic", "discord", "job_cancel"),
    "web_search": ("web_search", "browser", "job_cancel"),
}
_BROWSER_HINT_RE = re.compile(r"\b(navegador|browser|google|opera|pestaña|pestana)\b",
                             re.IGNORECASE)


def _prune_tools_for(intent_name: str, tools: dict, user_input: str = "") -> dict:
    keep = _PLANNER_TOOL_KEEP.get(intent_name or "")
    if not keep or not tools:
        return tools
    if _BROWSER_HINT_RE.search(user_input or ""):
        return tools
    pruned = {n: t for n, t in tools.items() if n.startswith(keep)}
    return pruned or tools


def _bare_artist(text: str) -> str:
    """El texto ES solo el nombre de un artista conocido (sin verbo ni pregunta).

    'Saja Boys' / 'RAMMA' / 'Paulo Londra' pelados = poner musica, no describir.
    El match debe cubrir TODO el texto (asi 'me gusta RAMMA' o
    'quien es RAMMA' siguen yendo a chat).
    """
    t = " ".join((text or "").split())
    if not t or len(t.split()) > 4:
        return ""
    q = _GONNA_FLY_RE.sub("Gonna Fly Now", t)
    for name, rx in _KNOWN_ARTISTS:
        if rx is None:
            if name == "Paulo Londra":
                m = _PAULO_RE.search(q)
            elif name == "Skillet":
                m = _SKILLET_RE.search(q)
            else:
                continue
        else:
            m = rx.search(q)
        if m and q.strip().lower() == (m.group(0) or "").strip().lower():
            return name
    return ""


_FACTUAL_Q_RE = re.compile(
    r"\b(qui[eé]n|qui[eé]nes|cu[aá]l|cu[aá]les|cu[aá]ndo|d[oó]nde|cu[aá]nto|cu[aá]ntos|cu[aá]ntas|por\s+qu[eé])\b",
    re.IGNORECASE,
)
# Segunda persona = pregunta A CLAUDIO (edad/nombre/opinion): responde la
# persona, no se busca en la web ("cuántos años tenés" no es geografía).
_YOU_RE = re.compile(
    r"\b(ten[eé]s|tienes|llam[áa]s|llamas|sos|eres|cre[eé]s|opin[áa]s|tu|tus)\b",
    re.IGNORECASE,
)
# "¿Qué pasó en X?" (con o sin tilde): evento a verificar. Pero no el
# saludo "¿qué pasó / qué te pasa?" (sin lugar/fecha/marca de sustancia).
_QUE_PASO_RE = re.compile(r"\bqu[eé]\s+(pas[oó]|pasa|pasaba|ocurri[oó]|sucedi[oó])\b",
                          re.IGNORECASE)
_QUE_TE_RE = re.compile(r"\bqu[eé]\s+(te|me)\b", re.IGNORECASE)
_SUSTANCIA_RE = re.compile(r"\b(en|del|al|ayer|hoy|esta|este|ese|esa)\b|\d",
                           re.IGNORECASE)


def _looks_factual(text: str) -> bool:
    """¿Pregunta factual que conviene VERIFICAR en la web?

    Principio: no afirmar lo que no se sabe (fue el papelon de Madrid).
    Saludos, hora/fecha (sistema), musica e identidad van por sus vias.
    """
    t = " ".join((text or "").split())
    if len(t.split()) < 3:
        return False
    low = t.lower()
    if re.search(r"\b(musica|música|canci[oó]n|tema|playlist|pelis?|m!|ramma)\b", low):
        return False
    if re.search(r"\bhora\b|\bfecha\b", low):
        return False
    if _YOU_RE.search(t):
        return False
    if _QUE_TE_RE.search(t):
        return False
    if _QUE_PASO_RE.search(t):
        return bool(_SUSTANCIA_RE.search(t))
    return bool(_FACTUAL_Q_RE.search(t))


def fast_path_intent(text: str):
    """Devuelve Intent sin LLM para pedidos obvios de musica/Discord. None si no aplica."""
    t = (text or "").lower()
    # Web primero: "buscá en google X" NO es música (intent web_search separado).
    # Deterministico (no se deja al LLM): si no hay sustantivos de musica.
    if (re.search(r"\b(google|internet|navegador)\b", t)
            and not re.search(r"\b(musica|música|canci[oó]n|tema|playlist|pelis?|pon[eé]|reproduc)", t)):
        return Intent(name="web_search", confidence=0.9, entities={})
    # Temas web (partidos, noticias, clima): sin sustantivos de musica no es musica.
    if (re.search(r"\b(partido|resultado|noticias?|clima|pron[oó]stico|elecciones?|d[oó]lar|precio|final|copa|mundial|libertadores)\b"
                  r"|quién\s+(es|ganó)|cuándo\s+juega|dónde\s+queda", t)
            and not re.search(r"\b(musica|música|canci[oó]n|tema|playlist|pelis?|pon[eé]|reproduc)", t)):
        return Intent(name="web_search", confidence=0.9, entities={})
    if control_command(text) is not None:
        return Intent(name="discord_command", confidence=0.97, entities={})
    if _FAST_CMD_RE.search(t):
        return Intent(name="discord_command", confidence=0.99, entities={})
    if re.search(r"\b(qu[eé]\s+hora\s+es|la\s+hora|qu[eé]\s+d[ií]a\s+es|qu[eé]\s+fecha|la\s+fecha|hoy\s+es|que\s+dia)\b", t):
        return Intent(name="system_time", confidence=0.99, entities={})
    # Nombre de artista solo ("Saja Boys", "RAMMA"): es para PONER, no para describir.
    if _bare_artist(t):
        return Intent(name="music_search", confidence=0.9, entities={})
    # Temas canonicos sin verbo ("más de Rocky", "eye of the tiger" pelado):
    # van a musica directo, no a chat a decir "no tengo información".
    tq = _GONNA_FLY_RE.sub("Gonna Fly Now", _ROCKY_RE.sub("Gonna Fly Now", t)).lower()
    if "gonna fly now" in tq or "eye of the tiger" in tq:
        return Intent(name="music_search", confidence=0.9, entities={})
    # "más de X" / "música de X" con X conocido ("Más de Rackynolds, Oro beso"):
    # es pedido de musica aunque no haya verbo de poner.
    if re.search(r"\bm[áa]s\s+de\b", t) and _has_known_music(t):
        return Intent(name="music_search", confidence=0.9, entities={})
    if (re.search(r"\bm[uú]sica\b|\bcanci[óo]n(es)?\b|\btemas?\b", t)
            and _has_known_music(t)):
        return Intent(name="music_search", confidence=0.9, entities={})
    m = _FAST_MUSIC_RE.search(t)
    if not m:
        return None
    hit = (m.group(0) or "").lower()
    # Verbos de control fuertes: casi siempre son musica (pausalo, reanudalo, salta, frenalo...).
    # "para" solo vale con sufijo (paralo/parala) para no confundir "para la tarea".
    _STRONG = ("paus", "pauz", "reanud", "resume", "salt", "skip", "siguiente", "fren", "deten", "silenci",
                "contin", "sigue", "otra")
    if hit.startswith(_STRONG):
        return Intent(name="music_search", confidence=0.9, entities={})
    if hit.startswith("par") and hit not in ("para", "par"):
        return Intent(name="music_search", confidence=0.9, entities={})
    if ("musi" in t or "tema" in t or "canci" in t or "ramma" in t or "tranqui" in t
            or "playlist" in t or "album" in t or "álbum" in t
            or ("lista" in t and ("mis" in t or "musi" in t or "playlist" in t
                                  or "canci" in t or "tema" in t))
            or "m!" in t or "pon" in t or "reproduc" in t
            # "buscá X" solo vale con compañero de musica (artista/tema/playlist):
            # "busca el rato de Boca" NO es musica (era el hueco del Boca-Racing).
            or ("busca" in t and ("musi" in t or "tema" in t or "canci" in t
                                  or "playlist" in t or "album" in t or "álbum" in t
                                  or bool(_query_artist(t))))):
        return Intent(name="music_search", confidence=0.9, entities={})
    return None


class Intent(BaseModel):
    name: str
    confidence: float
    entities: Dict[str, Any] = {}


class ActionStep(BaseModel):
    tool: str
    args: Dict[str, Any]
    depends_on: List[int] = []
    retry_policy: Dict[str, Any] = {}


class ConversationContext(BaseModel):
    relevant_memories: List[Dict[str, Any]] = []


class CLAUDIOState(TypedDict):
    user_input: str
    intent: Optional[Intent]
    context: ConversationContext
    plan: List[ActionStep]
    attempts: int
    results: Dict[str, Any]
    response: str
    metadata: Dict[str, Any]


def _split_off_sentences(buf: str):
    parts = re.split(r"(?<=[.!?…])\s+", buf)
    if len(parts) <= 1:
        return [], buf
    *complete, rest = parts
    return [p.strip() for p in complete if p.strip()], rest


# Incluye como suena por voz/STT: "Carby/Carbi/Ramba" siempre es RAMMA acá.
_RAMA_RE = re.compile(r"\bram+a\b|\bcarb[yi]\b|\bramba\b", re.IGNORECASE)
_PAULO_RE = re.compile(
    r"\bpa[bu]l[ao]\s+londra\b|\bpobl[ao]\s+londr?a\b"
    r"|\bpobl[ao]dondra\b|\bpoblolondra\b|\bpobl[ao]\s+(?:de\s+)?londra\b",
    re.IGNORECASE,
)
# "Ski Jet / Skyshet / S-K-I-L-E-T" = Skillet, banda de rock.
_SKILLET_RE = re.compile(
    r"\bskijet\b|\bskyjet\b|\bskyshet\b|\bskilet\b|\bskillets?\b"
    r"|\bsquilet\b|\besquilet\b|\beskillet\b|\bskidget\b|\bskidjet\b"
    r"|\bkitschets?\b|\bkitshets?\b|\bkischets?\b",
    re.IGNORECASE,
)
_RISE_RE = re.compile(r"\braze\b|\braice\b", re.IGNORECASE)
# "Saga/Aga Boys" (STT) = Saja Boys (KPop Demon Hunters).
# Variante truncada "Saja B" (se corta "boys" al soltar la tecla): solo "saja"
# porque "saga B..." podria ser "la saga Batman/Bond" y "aga" no es palabra.
_SAJA_BOYS_RE = re.compile(r"\b(saga|aga|saja)\s+boys?\b|\bsaja\s+b\b", re.IGNORECASE)
_JOIN_HYPHEN_RE = re.compile(r"\b(?:[A-Za-z]\s*[-–]\s*){2,}[A-Za-z]\b")


def _join_spelled_word(m) -> str:
    return re.sub(r"[\s\-–]", "", m.group(0)).upper()


_BAND_REF_RE = re.compile(
    r"\besa banda\b|\bese artista\b|\bese cantante\b|\bese grupo\b"
    r"|\bla banda\b|\bel grupo\b",
    re.IGNORECASE,
)


def remember_artist(name) -> None:
    """Guarda el último artista que sonó (para 'esa banda...' posterior)."""
    try:
        from core.config import settings
        from core.store import Store
        n = (name or "").strip().split(",")[0].strip()
        if n:
            Store(settings.memory.vault_path / "data").remember(
                "ultimo_artista", n, source="musica")
    except Exception:
        pass


def recall_artist():
    try:
        from core.config import settings
        from core.store import Store
        return Store(settings.memory.vault_path / "data").recall("ultimo_artista")
    except Exception:
        return None
_YT_SPAM_RE = re.compile(
    r"\bgracias por ver (este |el )?video\b|\bno olvides suscribirte\b"
    r"|\bsuscrib[ei]te\b|\bsuscribirse\b|\bdale (like|me gusta)\b"
    r"|\bdeja tu like\b|\bhasta el pr[óo]ximo video\b"
    r"|\bnos vemos en el pr[óo]ximo video\b",
    re.IGNORECASE,
)
_BARE_BANG_CMD_RE = re.compile(
    r"(^|[\s\"'¿¡(\[])[!¡]+(play|p|pause|resume|unpause|skip|queue|stop|clear)\b",
    re.IGNORECASE,
)
_M_SPACE_CMD_RE = re.compile(r"\bm\s+(play|p|pause|resume|skip|queue|stop)\b", re.IGNORECASE)
_EME_PLAY_RE = re.compile(
    r"\b(em+e|en)\s+(!?(play|p|pause|resume|skip|queue|stop))\b",
    re.IGNORECASE,
)
_BARE_WORD_CMD_RE = re.compile(
    r"(^|[\s;])((play|pause|resume|unpause|skip|queue|stop))\s+(https?://\S+|[\w\-\.:/?=&%#]+.*)",
    re.IGNORECASE,
)
_CLAUDIO_TYPO_RE = re.compile(
    r"\b(charvis|c[aá]rvis|karvis|dclaudio|zarvis|yarvis|harvis|garvis|jarviz|jarbis|sharvis|charris|carris|yarris|shari|sharis)\b",
    re.IGNORECASE,
)
# "renovar/renueva" siempre es "reanudar" acá (el STT los confunde).
_REANUDA_RE = re.compile(r"\brenov[ao][a-z]*\b|\brenuev[ao][a-z]*\b", re.IGNORECASE)
_SPELLED_RE = re.compile(
    r"\b[A-Za-z]\.(?:\s*[A-Za-z]\.){1,}\s*[A-Za-z]\.?(?![A-Za-z])"
    r"|\b(?:[A-Z]\s+){2,}[A-Z]\b"
)
_SPELLED_OK = set("CLAUDIOCHYBGT")
_WAKE_STRIP_RE = re.compile(
    r"\b(charly|charli|sharvis|jarviz|harvis|yarvis|garvis|jarbis|"
    r"charvis|carvis|karvis|dclaudio|zarvis|claudio|shari)\b",
    re.IGNORECASE,
)


_FALSE_START_RE = re.compile(
    r"^(?:(?:par[áa]|alto|basta|eh+|mmm+|este+|che|bueno|perd[oó]n|o\s+sea)\b[,\s]*)*"
    r"no\b\s*,\s*(?=\S)",
    re.IGNORECASE,
)


def strip_false_start(text: str) -> str:
    """Saca el arranque fallido ('pará, no, abrí Chrome' -> 'abrí Chrome').

    Solo cuando hay coma tras el 'no': 'no pongas X' (negación real) no se toca.
    """
    t = text or ""
    return _FALSE_START_RE.sub("", t).strip() or t


def strip_before_wake(text: str) -> str:
    """Todo lo anterior al último 'Claudio' se ignora (balbuceo previo del usuario).

    'Por qué puso eso? CLAUDIO, poné cumbia' -> 'CLAUDIO, poné cumbia'.
    Sin 'claudio' no se toca nada.
    """
    t = text or ""
    last = None
    for m in _WAKE_STRIP_RE.finditer(t):
        last = m
    if last is None:
        return t
    return t[last.start():].lstrip(" ,.!?¿¡-") or t


def _spelled_to_claudio(m) -> str:
    letters = re.sub(r"[^A-Za-z]", "", m.group(0)).upper()
    # Whisper a veces pierde letras ("C.C.C.S.V." sin R): basta con 2+ letras
    # nucleo (J/A/R/V/I/S). F.B.I./U.S.A. quedan fuera por el set permitido.
    if (len(letters) >= 3 and set(letters) <= _SPELLED_OK
            and len(set(letters) & set("CLAUDIO")) >= 2):
        return "CLAUDIO"
    return m.group(0)


def normalize_user_text(text: str) -> str:
    """Corrige errores sistemáticos de STT/tipeo antes de entender el pedido.

    - rama/ramaa (una m) -> RAMMA (doble M, pedido explícito de Señor)
    - !play/!pause/... sin m -> m!play/m!pause/...
    - "C.R.V.S." / "Carvis" (STT deletrea/yerra Claudio) -> CLAUDIO
    """
    t = text or ""
    if not t.strip():
        return t
    t = _SPELLED_RE.sub(_spelled_to_claudio, t)
    t = _CLAUDIO_TYPO_RE.sub("CLAUDIO", t)
    t = _REANUDA_RE.sub("reanudar", t)
    t = strip_before_wake(t)
    t = strip_false_start(t)
    t = _JOIN_HYPHEN_RE.sub(_join_spelled_word, t)
    # Tartamudeo con guiones ("re-re-re-anuda", "sa-sa-salta"): colapsa a uno.
    # Despues une el guion corto ("re-anuda" -> "reanuda"). "rock-and-roll"
    # se preserva (fragmentos de 3+ letras no se tocan).
    t = re.sub(r"\b(\w{1,3})(?:-\1){1,}-?", r"\1", t)
    t = re.sub(r"\b([A-Za-z]{1,2})[-–](?=[A-Za-z])", r"\1", t)
    t = _RAMA_RE.sub("RAMMA", t)
    t = _PAULO_RE.sub("Paulo Londra", t)
    t = _SKILLET_RE.sub("Skillet", t)
    t = _SAJA_BOYS_RE.sub("Saja Boys", t)
    t = _OTRO_BESO_RE.sub("Otro beso", t)
    t = re.sub(r"\b(vichero|vichera|viyero|viyera|biyero|biyera|villegru|vilegru)\b", "villero", t,
               flags=re.IGNORECASE)
    t = re.sub(r"\bpauz[aoá]\b", "pausa", t, flags=re.IGNORECASE)
    # "reposi" (STT) = "reproducila" (no es palabra); "reponé" (vos) = "poné".
    # "repone" sin tilde NO se toca (él: "se repone de la lesión").
    t = re.sub(r"\brepos[íi]\b", "reproducila", t, flags=re.IGNORECASE)
    t = re.sub(r"\breponé\b", "poné", t, flags=re.IGNORECASE)
    t = re.sub(r"\bliscord\b", "Discord", t, flags=re.IGNORECASE)
    t = re.sub(r"\bsil\b", "sin", t, flags=re.IGNORECASE)
    t = _RISE_RE.sub("Rise", t)
    t = _ROCKY_RE.sub("Gonna Fly Now", t)
    t = _YT_SPAM_RE.sub(" ", t)
    t = " ".join(t.split())
    t = _EME_PLAY_RE.sub(lambda m: f"m!{m.group(2).lstrip('!')}", t)
    t = _BARE_BANG_CMD_RE.sub(lambda m: f"{m.group(1)}m!{m.group(2)}", t)
    t = _M_SPACE_CMD_RE.sub(lambda m: f"m!{m.group(1)}", t)
    return t


def _fix_discord_text(text: str) -> str:
    """Garantiza que un comando Discord lleve el prefijo m! (Jockie lo exige)."""
    t = (text or "").strip()
    if not t:
        return t
    # "!play X" -> "m!play X"
    t = _BARE_BANG_CMD_RE.sub(lambda m: f"{m.group(1)}m!{m.group(2)}", f" {t}").strip()
    t = _M_SPACE_CMD_RE.sub(lambda m: f"m!{m.group(1)}", t)
    # "play <link>" / "pause" sueltos sin prefijo -> "m!play ..." / "m!pause"
    m = re.match(
        r"^(play|p|pause|resume|unpause|skip|queue|stop|clear)\b(.*)$",
        t, re.IGNORECASE,
    )
    if m:
        cmd = m.group(1).lower()
        if cmd == "p":
            cmd = "p"
        rest = m.group(2).strip()
        t = f"m!{cmd} {rest}".strip()
    # "m!play" -> "m!p" (alias corto que usa la memoria para playlists)
    t = re.sub(r"^m!play\b", "m!p", t, flags=re.IGNORECASE)
    return " ".join(t.split())


def _canon_args(args) -> Dict[str, Any]:
    a = dict(args or {})
    if isinstance(a.get("message"), str):
        a["message"] = _fix_discord_text(a["message"])
    if isinstance(a.get("command"), str):
        a["command"] = _fix_discord_text(a["command"])
    # music_play/music_search: rama (una m) -> RAMMA (doble M)
    for k in ("query", "artist"):
        if isinstance(a.get(k), str) and a[k].strip():
            a[k] = _RAMA_RE.sub("RAMMA", a[k])
    return a


def _canon_step(tool: str, args) -> tuple:
    a = _canon_args(args)
    if tool == "discord_command" and isinstance(a.get("command"), str):
        return ("discord_send", a["command"])
    if tool == "discord_user_send":
        return ("discord_send", a.get("message", ""))
    return (tool, json.dumps(a, sort_keys=True, ensure_ascii=False))


def _format_step(key: str, result: Any, history: list, idx: int) -> str:
    tool = history[idx] if idx < len(history) else "?"
    text = str(result)
    if len(text) > 250:
        text = text[:250] + "..."
    return f"{key} {tool}: {text}"


# Solo estas herramientas representan ACCIONES sobre el mundo. Una búsqueda
# (mymusic_search, web, etc.) con ok:true NO es algo "hecho": confirmarla
# con "Listo." es mentir (pasó con "cuáles son mis 3 temas").
ACTION_TOOLS = {
    "discord_user_send", "discord_command", "discord_send", "music_control",
    "music_play", "launch_app", "close_app", "focus_window",
    "run_shell", "job_start", "job_cancel", "type_text", "press_hotkey",
    "speak", "manage_calendar", "manage_tasks",
}


_SHOW_RE = re.compile(
    r"\b(mostr|mostrame|muestrame|enseñ|decime|contame|explicame|detall|"
    r"qu[eé]\s+(hiciste|estas?\s+haciendo|est[aá]\s+sonando|suena|pas[ao])|"
    r"enseñame\s+lo\s+que\s+haces)\b",
    re.IGNORECASE,
)


def wants_show(text: str) -> bool:
    """¿Pidió VER lo que hace? Por defecto NO: todo corre en 2do plano silencioso."""
    return bool(_SHOW_RE.search(text or ""))


def _local_confirm(results: Dict[str, Any], history: list, sent=(), user_text: str = "") -> str:
    """Confirmación sin LLM: solo si una ACCIÓN terminó con ok:true.

    Evita el peor fallo posible: la acción salió bien pero se le dice al
    usuario que falló porque murió el LLM. Y evita mentir "Listo." ante una
    simple búsqueda (pregunta sin acción -> se dice la verdad).
    Si se mandó un tema (m!p), manda el TÍTULO de lo que suena, no el control.
    """
    keys = _ordered_step_keys(results or {})
    last_ok_tool = ""
    for i, k in enumerate(keys):
        r = (results or {}).get(k)
        if isinstance(r, dict) and r.get("ok") is True:
            last_ok_tool = history[i] if i < len(history) else ""
    title = ""
    opts = []
    for k in keys:
        r = (results or {}).get(k)
        if not isinstance(r, dict):
            continue
        pick = r.get("pick")
        if isinstance(pick, dict) and pick.get("title"):
            title = str(pick["title"])
        items = []
        if isinstance(pick, dict) and pick.get("title"):
            items.append(pick)
        for t in (r.get("tracks") or []) + (r.get("results") or []) + (r.get("playlists") or []):
            if isinstance(t, dict) and t.get("title"):
                items.append(t)
                if not title:
                    title = str(t["title"])
        for t in items:
            label = str(t.get("title", "")).strip()
            artists = str(t.get("artists", "") or "").strip()
            if artists:
                label += f" de {artists}"
            if label and label not in opts:
                opts.append(label)
            if len(opts) >= 3:
                break
        if len(opts) >= 3:
            break
    played = [c for c in (sent or [])
              if re.match(r"(?i)^m!(p|play)\b", (c or "").strip())]
    played_url = ""
    if played:
        m = re.search(r"https?://\S+", played[0])
        if m:
            played_url = m.group(0).rstrip(").,")
    if played_url:
        # El titulo TIENE que ser lo que sono (no el primer resultado):
        # "poné Villeros" no puede confirmar "like".
        for k in keys:
            r = (results or {}).get(k)
            if not isinstance(r, dict):
                continue
            for t in (r.get("tracks") or []) + (r.get("results") or []) + (r.get("playlists") or []):
                if (isinstance(t, dict)
                        and str(t.get("url", "") or "").strip() == played_url
                        and str(t.get("title", "") or "").strip()):
                    title = str(t["title"]).strip()
                    break
            else:
                continue
            break
    if played and title:
        return f"Listo, {title} en segundo plano."
    tools_done = [history[i] for i, k in enumerate(keys)
                  if isinstance((results or {}).get(k), dict)
                  and (results or {})[k].get("ok") is True
                  and i < len(history)]
    if "job_cancel" in tools_done:
        return "Cancelado."
    for c in reversed(list(sent or [])):
        m = re.match(r"(?i)^m!(pause|resume|unpause|skip|queue|stop|clear)\b", (c or "").strip())
        if m:
            return {"pause": "Pausado.", "resume": "Siguiendo.", "unpause": "Siguiendo.",
                    "skip": "Siguiente.", "queue": "Listo.", "stop": "Frenado.",
                    "clear": "Frenado."}[m.group(1).lower()]
    # Sin nada enviado no se afirma nada: si hay resultados, se presentan
    # como opciones (fail-safe, 0 tokens). Nunca "Listo, X en segundo plano".
    # Y si TODO lo encontrado es lo vetado/excluido, se dice, no se ofrece.
    if opts:
        excluded = excluded_words(user_text)
        ok_opts = []
        for o in opts:
            if _blocked_title(o) or _title_excluded(o, excluded):
                continue
            ok_opts.append(o)
        if ok_opts:
            return f"Encontré: {', '.join(ok_opts)}. ¿Cuál pongo?"
        return (f"Todo lo que encuentro es {opts[0]}, que me pediste evitar. "
                f"¿Te busco otro tema?")
    if last_ok_tool and last_ok_tool in ACTION_TOOLS:
        return "Listo."
    return "Lo siento, tuve un problema generando la respuesta. Intenta de nuevo."


def _claudio_version() -> str:
    try:
        import subprocess
        from pathlib import Path
        r = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            capture_output=True, text=True, timeout=10,
            cwd=Path(__file__).resolve().parent.parent,
        )
        return (r.stdout or "").strip() or "dev"
    except Exception:
        return "dev"


def _ordered_step_keys(results: Dict[str, Any]) -> List[str]:
    out = []
    for key in results:
        if key.startswith("step_"):
            try:
                out.append((int(key[5:]), key))
            except ValueError:
                pass
    return [key for _, key in sorted(out)]


_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
          "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def _ahora_str() -> str:
    from datetime import datetime
    now = datetime.now()
    return (f"{_DIAS[now.weekday()]} {now.day} de {_MESES[now.month - 1]} "
            f"de {now.year}, {now.hour:02d}:{now.minute:02d}")


class CLAUDIOBrain:
    def __init__(self, memory: MemorySystem, plugin_registry: PluginRegistry):
        self.memory = memory
        self.plugin_registry = plugin_registry
        self.llm_main = build_chat_model("main")
        self.llm_fast = build_chat_model("fast")
        self._chat_cache: Dict[str, Any] = {}
        self._last_facts_ts: float = 0.0
        self.graph = self._build_graph()

    def _build_graph(self) -> StateGraph:
        workflow = StateGraph(CLAUDIOState)

        workflow.add_node("perceive", self._perceive)
        workflow.add_node("chat", self._chat)
        workflow.add_node("understand", self._understand)
        workflow.add_node("retrieve", self._retrieve)
        workflow.add_node("plan", self._plan)
        workflow.add_node("execute", self._execute)
        workflow.add_node("verify", self._verify)
        workflow.add_node("synthesize", self._synthesize)
        workflow.add_node("speak", self._speak)

        workflow.set_entry_point("perceive")

        workflow.add_conditional_edges(
            "perceive",
            self._route_after_perceive,
            {"chat": "chat", "full": "understand"},
        )
        workflow.add_edge("chat", "speak")
        workflow.add_edge("understand", "retrieve")
        workflow.add_edge("retrieve", "plan")
        workflow.add_conditional_edges(
            "plan",
            self._route_after_plan,
            {"act": "execute", "done": "synthesize"},
        )
        workflow.add_edge("execute", "verify")
        workflow.add_conditional_edges(
            "verify",
            self._should_replan,
            {"replan": "plan", "giveup": "synthesize"},
        )
        workflow.add_edge("synthesize", "speak")
        workflow.add_edge("speak", END)

        serde = JsonPlusSerializer(allowed_msgpack_modules=[
            ("core.brain", "Intent"),
            ("core.brain", "ConversationContext"),
            ("core.brain", "ActionStep"),
        ])
        return workflow.compile(checkpointer=MemorySaver(serde=serde))

    async def _perceive(self, state: CLAUDIOState) -> CLAUDIOState:
        # Normalizar ANTES de todo: rama->RAMMA, !play->m!play (errores de STT).
        try:
            state["user_input"] = normalize_user_text(state.get("user_input") or "")
        except Exception:
            pass
        await emit_event("on_perceive", state["user_input"])
        return state

    def _route_after_perceive(self, state: CLAUDIOState) -> Literal["chat", "full"]:
        # Controles y atajos musicales SIEMPRE al circuito completo: por charla
        # solo saben hablar ("Enseguida..." sin mandar nada = mentira).
        # Va primero: la ruta es por intención, haya tools o no.
        try:
            raw = state.get("user_input") or ""
            if control_command(raw) is not None or fast_path_intent(raw) is not None:
                return "full"
        except Exception:
            pass
        try:
            if _is_correction(state.get("user_input") or ""):
                return "chat"
        except Exception:
            pass
        if not self.plugin_registry.get_all_tools():
            return "chat"
        try:
            # Pregunta factual: se VERIFICA (web) en vez de afirmar de memoria.
            if _looks_factual(state.get("user_input") or ""):
                return "full"
        except Exception:
            pass
        text = (state.get("user_input") or "").lower()
        for intent in self.plugin_registry.get_all_intents().values():
            for rx in getattr(intent, "patterns", []):
                try:
                    if re.search(rx, text):
                        return "full"
                except re.error:
                    continue
        return "chat"

    def _format_memories(self, memories: List[Dict[str, Any]], max_chars: int = 300) -> str:
        if not memories:
            return "(ninguna)"
        lines = []
        for m in memories:
            content = str(m.get("content", "")).strip().replace("\n", " ")
            if len(content) > max_chars:
                content = content[:max_chars] + "..."
            lines.append(f"- {content}")
        return "\n".join(lines)

    def _parse_plan(self, text: str) -> List[Any]:
        try:
            data = json.loads(text)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return [data]
        except Exception:
            pass
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
                if isinstance(data, list):
                    return data
            except Exception:
                pass
        return []

    async def _recent_history(self, limit: int = 4, max_chars: int = 400) -> str:
        try:
            convs = await self.memory.get_recent_conversations(limit=limit)
        except Exception:
            return "(sin historial)"
        turns = []
        for c in reversed(convs):
            content = str(c.get("content", "")).strip()
            if len(content) > max_chars:
                content = content[:max_chars] + "..."
            if content:
                turns.append(content)
        return "\n\n".join(turns) if turns else "(sin historial)"

    async def _chat(self, state: CLAUDIOState, on_sentence=None) -> CLAUDIOState:
        # Cuentas exactas sin LLM ("¿cuánto es 5 más 5?"): número pelado, al instante.
        try:
            calc = try_calc_answer(state["user_input"])
        except Exception:
            calc = None
        if calc is not None:
            state["response"] = calc
            await emit_event("on_response_synthesized", state["response"])
            return state
        # Repetidas ("me escuchas bien?"): respuesta cacheada, 0 tokens.
        cache_key = state["user_input"].strip().lower()
        if _chat_cacheable(state["user_input"]):
            hit = self._chat_cache.get(cache_key)
            if hit and time.time() - hit[1] < _CHAT_CACHE_TTL:
                state["response"] = hit[0]
                if on_sentence is not None:
                    buf = hit[0]
                    while buf.strip():
                        complete, buf = _split_off_sentences(buf)
                        if not complete:
                            await on_sentence(buf.strip())
                            break
                        for sentence in complete:
                            await on_sentence(sentence)
                await emit_event("on_response_synthesized", state["response"])
                return state
        # Saludos/acks cortos: sin RAG (ahorra el embedding + su latencia).
        if len(state["user_input"]) < 20 and "?" not in state["user_input"]:
            memories = []
            history = await self._recent_history()
        else:
            # RAG + historial en paralelo (antes secuencial: 2x latencia Ollama).
            memories, history = await asyncio.gather(
                self.memory.search(state["user_input"], top_k=settings.memory.top_k),
                self._recent_history(),
            )
        system_prompt = f"""Eres CLAUDIO, el asistente personal de Señor. Respondes en {settings.claudio.response_language}, de forma concisa y natural.
Hablas como en una llamada: 1 a 3 frases cortas y directas. Solo desarrolla si te lo piden.
{PERSONA}
PROHIBIDO responder en inglés u otro idioma: Señor solo habla español, SIEMPRE español rioplatense.
Voseo SIEMPRE (vos, querés, poné, decime, mirá): prohibido el tuteo (quieres, pon, dime, mira).
El usuario es varón: masculino SIEMPRE (tratalo de "señor"). Ante insultos en joda: ironía elegante, jamás insultes ni etiquetes de vuelta.
Apodos (amigos/familia): SOLO los anotados en tu vault (usuario.md/ordenes.md), en tercera persona cuando el usuario los nombre. Al USUARIO nunca le pones apodo: a él, "señor".
Tu versión real en ejecución: {_claudio_version()}. Si preguntan tu versión, da EXACTAMENTE esta.
Nunca digas frases de YouTuber ("gracias por ver el video", "suscribete", "dale like").
PROHIBIDO inventar biografias o datos de artistas/temas que no conoces: si te preguntan por algo que no esta en tu memoria ni conoces de verdad, decilo en 1 frase ("Ese no lo tengo, señor") en vez de improvisar una descripcion generica.
PROHIBIDO inventar listas de canciones/discos/playlists. PROHIBIDO decir "Listo"/"Enseguida" o confirmar acciones por esta via: por chat no ejecutas nada, solo conversas.
Fecha y hora actual: {_ahora_str()}.
REGLA DE IDENTIDAD: los datos de memoria describen AL USUARIO (Señor, 18 años), NUNCA a ti. Jamas digas que tu te llamas Señor ni que tienes 18 años: tu te llamas CLAUDIO y eres una IA.
Conversacion reciente (para entender referencias como "eso", "de vuelta", "lo de antes"):
{history}
Memoria relevante del vault (usala si responde a la pregunta, si no, ignorala):
{self._format_memories(memories)}"""
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=state["user_input"]),
        ]
        # Charla simple con modelo barato (120b se reserva para tools/síntesis).
        try:
            if on_sentence is None:
                response = await self.llm_fast.ainvoke(messages)
                state["response"] = scrub_response(response.content)
            else:
                await self._stream_chat(messages, state, on_sentence, model=self.llm_fast)
                state["response"] = scrub_response(state["response"])
        except Exception as e:
            logger.error("chat_failed", error=str(e))
            state["response"] = "Lo siento, tuve un problema generando la respuesta. Intenta de nuevo."
        if _chat_cacheable(state["user_input"]) and state["response"] \
                and not state["response"].startswith("Lo siento"):
            self._chat_cache[cache_key] = (state["response"], time.time())
            if len(self._chat_cache) > 100:
                self._chat_cache.pop(next(iter(self._chat_cache)))
        await emit_event("on_response_synthesized", state["response"])
        return state

    async def _understand(self, state: CLAUDIOState) -> CLAUDIOState:
        user_input = state["user_input"]
        intents = self.plugin_registry.get_all_intents()

        # Via rapida: 0 llamadas LLM para musica/comandos obvios.
        try:
            fast = fast_path_intent(user_input)
            if fast is not None:
                state["intent"] = fast
                await emit_event("on_intent_classified", state["intent"])
                return state
        except Exception:
            pass

        system_prompt = f"""Clasifica la intención del usuario. Intents disponibles:
{self._format_intents(intents)}

Responde SOLO con JSON: {{"name": "intent_name", "confidence": 0.95, "entities": {{}}}}"""

        # Pregunta pendiente del turno anterior: contexto solo para entender,
        # las herramientas siempre ven el input limpio.
        pend = _load_pending_question()
        human_in = (f"Antes preguntaste: '{pend}'. Responde a eso.\n"
                    f"Mensaje actual: {user_input}") if pend else user_input
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=human_in),
        ]

        try:
            response = await self.llm_fast.ainvoke(messages)
            state["intent"] = self._parse_intent(response.content, intents, user_input)
        except Exception as e:
            logger.error("intent_classification_failed", error=str(e))
            state["intent"] = Intent(name="unknown", confidence=0.0, entities={})

        if state["intent"] is None or state["intent"].name == "unknown":
            try:
                history = await self._recent_history(limit=4, max_chars=400)
                retry_messages = [
                    SystemMessage(content=system_prompt),
                    HumanMessage(
                        content=f"Conversacion reciente:\n{history}\n\n"
                        f"Mensaje actual (puede referirse a lo anterior con palabras como "
                        f"'eso', 'devuelta', 'dale', 'si'): {user_input}"
                    ),
                ]
                response = await self.llm_fast.ainvoke(retry_messages)
                state["intent"] = self._parse_intent(response.content, intents, user_input)
            except Exception as e:
                logger.error("intent_retry_failed", error=str(e))

        await emit_event("on_intent_classified", state["intent"])
        return state

    def _parse_intent(self, text: str, intents: Dict[str, Any], user_input: str) -> Intent:
        data = None
        try:
            data = json.loads(text)
        except Exception:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(0))
                except Exception:
                    data = None
        if isinstance(data, dict) and "name" in data:
            try:
                entities = data.get("entities", {})
                return Intent(
                    name=str(data.get("name", "unknown")),
                    confidence=float(data.get("confidence", 0.5)),
                    entities=entities if isinstance(entities, dict) else {},
                )
            except Exception:
                pass
        lowered = user_input.lower()
        for name, pattern in (intents or {}).items():
            for rx in getattr(pattern, "patterns", []):
                try:
                    if re.search(rx, lowered):
                        return Intent(name=name, confidence=0.6, entities={})
                except re.error:
                    continue
        return Intent(name="unknown", confidence=0.0, entities={})

    async def _stream_chat(self, messages, state: CLAUDIOState, on_sentence,
                           model=None) -> None:
        sentence_queue: asyncio.Queue = asyncio.Queue()
        DONE = object()
        state["response"] = ""
        model = model or self.llm_main

        async def producer():
            buf = ""
            try:
                async for chunk in model.astream(messages):
                    token = getattr(chunk, "content", "") or ""
                    state["response"] += token
                    buf += token
                    complete, buf = _split_off_sentences(buf)
                    for sentence in complete:
                        await sentence_queue.put(sentence)
            except Exception as e:
                logger.error("stream_chat_failed", error=str(e)[:150])
            finally:
                if buf.strip():
                    await sentence_queue.put(buf.strip())
                await sentence_queue.put(DONE)

        async def consumer():
            while True:
                item = await sentence_queue.get()
                if item is DONE:
                    break
                try:
                    await on_sentence(item)
                except Exception as e:
                    logger.warning("stream_speak_fallo", error=str(e)[:150])

        await asyncio.gather(producer(), consumer())

    async def quick_reply(self, user_input: str, on_sentence=None, thread_id: str = "call") -> str:
        user_input = normalize_user_text(user_input)
        memories = await self.memory.search(user_input, top_k=settings.memory.top_k)
        state: CLAUDIOState = {
            "user_input": user_input,
            "intent": None,
            "context": ConversationContext(relevant_memories=memories),
            "plan": [],
            "attempts": 0,
            "results": {},
            "response": "",
            "metadata": {"thread_id": thread_id, "mode": "quick"},
        }
        await self._chat(state, on_sentence=on_sentence)
        asyncio.create_task(self._safe_learn(state))
        return state["response"]

    async def _safe_learn(self, state: CLAUDIOState) -> None:
        try:
            await self._learn(state)
        except Exception as e:
            logger.warning("safe_learn_fallo", error=str(e)[:150])

    async def _retrieve(self, state: CLAUDIOState) -> CLAUDIOState:
        if not state["intent"] or state["intent"].name == "unknown":
            return state
        # Turno deterministico de musica (el plan sale sin LLM y la respuesta
        # sale de los resultados): el RAG (~1.7s en Ollama) no aporta nada.
        # Se salta para responder mas rapido.
        try:
            if state["intent"].name in ("music_search", "discord_command", "mymusic"):
                t0 = normalize_user_text(state["user_input"] or "")
                tools = (self.plugin_registry.get_all_tools() or {}) if self.plugin_registry else {}
                if len(_parse_compound(t0, state["intent"].name)) > 1:
                    state["context"].relevant_memories = []
                    return state
                step = fast_first_step(state["user_input"], state["intent"].name)
                if step is not None and tools and step.tool in tools:
                    state["context"].relevant_memories = []
                    return state
        except Exception:
            pass

        query = state["user_input"]
        # RAG semantico + contexto episodico en paralelo (antes secuencial).
        memories, episodic_context = await asyncio.gather(
            self.memory.search(query, top_k=settings.memory.top_k),
            self.memory.get_relevant_context(query, limit=2),
        )
        if episodic_context:
            # Agregar conversaciones relevantes como contexto adicional
            for ctx in episodic_context:
                memories.append({
                    "content": f"[Conversación previa]: {ctx['content']}",
                    "metadata": ctx["metadata"],
                    "distance": ctx["relevance"],
                    "score": ctx["relevance"],
                })

        state["context"].relevant_memories = memories
        await emit_event("on_memory_retrieved", memories)
        return state

    async def _ask_resolve(self, text: str) -> str:
        """'Tema más reproducido de X' -> 'Artista - Tema'. Una llamada chica o ''."""
        try:
            res = await self.llm_fast.ainvoke([
                SystemMessage(content="Respondes SOLO con 'Artista - Tema' del tema que piden "
                                      "(ejemplo: 'Paulo Londra - Tal Vez'). Nada más: "
                                      "sin comillas extra, sin explicación."),
                HumanMessage(content=(text or "")[:300]),
            ])
            ans = " ".join(str(res.content or "").split()).strip("\"'“” ").strip()
            if len(ans) < 4 or len(ans) > 80:
                return ""
            return ans
        except Exception:
            return ""

    async def _plan(self, state: CLAUDIOState) -> CLAUDIOState:
        tools = self.plugin_registry.get_all_tools()
        # Cancelar vale para cualquier intención (incluso unknown/chat).
        # Una sola vez por turno: repetir job_cancel ok es loop y tokens.
        try:
            hist = (state.get("metadata") or {}).get("tool_history") or []
            if "job_cancel" not in hist:
                step = _cancel_step(state.get("user_input") or "", tools or {})
                if step is not None:
                    state["plan"] = [step]
                    await emit_event("on_plan_created", state["plan"])
                    return state
        except Exception:
            pass
        if not state["intent"] or state["intent"].name == "unknown":
            state["plan"] = []
            return state
        # Hora/fecha local: sin LLM.
        try:
            step = _system_time_step(state["intent"].name, tools or {})
            if step is not None:
                state["plan"] = [step]
                await emit_event("on_plan_created", state["plan"])
                return state
        except Exception:
            pass

        if not tools:
            state["plan"] = []
            return state
        # Via rapida deterministica: el primer paso de musica no necesita LLM.
        # Pedidos compuestos ("quita y poné X") dejan cola ordenada en metadata.
        if not state["results"]:
            try:
                raw_in = _resolve_misma(state["user_input"] or "")
                t0 = normalize_user_text(raw_in)
                seq = _parse_compound(t0, state["intent"].name)
                if len(seq) > 1:
                    meta = state.get("metadata") or {}
                    state["metadata"] = meta
                    rest = list(seq)
                    step = None
                    while rest and step is None:
                        kind, val = rest.pop(0)
                        if kind == "resolve":
                            ans = await self._ask_resolve(state.get("user_input") or "")
                            if ans:
                                meta["resolved_query"] = ans
                            continue
                        step = _build_seq_item((kind, val), state, t0)
                    if step is not None and step.tool in tools:
                        meta["action_queue"] = rest
                        state["plan"] = [step]
                        await emit_event("on_plan_created", state["plan"])
                        return state
                step = fast_first_step(raw_in, state["intent"].name)
                if step is not None and step.tool in tools:
                    state["plan"] = [step]
                    await emit_event("on_plan_created", state["plan"])
                    return state
            except Exception:
                pass
        else:
            # Mitad de una compuesta: siguiente ítem de la cola (sin LLM).
            try:
                q = list((state.get("metadata") or {}).get("action_queue") or [])
                while q:
                    kind, val = q.pop(0)
                    if kind == "resolve":
                        # Averiguar QUÉ tema es antes de buscarlo (1 llamada chica).
                        ans = await self._ask_resolve(state.get("user_input") or "")
                        meta = state.get("metadata") or {}
                        if ans:
                            meta["resolved_query"] = ans
                            state["metadata"] = meta
                        meta2 = state.get("metadata") or {}
                        meta2["action_queue"] = q
                        state["metadata"] = meta2
                        continue
                    if kind == "send":
                        link = _first_link(state.get("results") or {},
                                           state.get("user_input") or "")
                        if not link:
                            continue
                        step = ActionStep(
                            tool="discord_user_send",
                            args={"message": f"m!p {link}"}, depends_on=[])
                    else:
                        step = _build_seq_item((kind, val), state,
                                               normalize_user_text(
                                                   state["user_input"] or ""))
                    if step is not None and step.tool in tools:
                        meta = state.get("metadata") or {}
                        meta["action_queue"] = q
                        state["metadata"] = meta
                        state["plan"] = [step]
                        await emit_event("on_plan_created", state["plan"])
                        return state
                (state.get("metadata") or {})["action_queue"] = []
            except Exception:
                pass
        # Automatismo: link encontrado -> m!stop/m!play al instante, 0 tokens.
        # El m!p sale INMEDIATO sin otra vuelta de LLM (es la accion repetida).
        try:
            auto = auto_music_step(state)
            if auto is not None and auto.tool in tools:
                state["plan"] = [auto]
                await emit_event("on_plan_created", state["plan"])
                return state
            if deterministic_done(state):
                state["plan"] = []
                await emit_event("on_plan_created", state["plan"])
                return state
        except Exception:
            pass
        intent = state["intent"]
        context = state["context"]
        hist = list((state.get("metadata") or {}).get("tool_history") or [])
        keys = _ordered_step_keys(state["results"])
        done = [_format_step(k, state["results"][k], hist, i) for i, k in enumerate(keys)]
        done_text = "\n".join(done) if done else "ninguno"
        from collections import Counter
        counts = Counter(hist)
        repeat_warn = ""
        for tool, n in counts.items():
            if n >= 2 and tool in ("music_search", "mymusic_search", "browser_read", "browser_open", "web_search"):
                repeat_warn = (f"\nYA BUSCASTE {n} VECES con {tool} y tienes resultados. "
                               f"ELEGI uno y termina con done. PROHIBIDO buscar de nuevo.")
                break
        if not repeat_warn and any(v >= 3 for v in counts.values()):
            repeat_warn = "\nYa ejecutaste 3+ pasos: TERMINA con done usando lo que tienes."
        orders = load_orders()
        shown_tools = _prune_tools_for(intent.name if intent else "",
                                       tools, state.get("user_input") or "")

        system_prompt = f"""Eres un planificador ReAct. Decide el PROXIMO paso (uno solo) para cumplir la intención.
Intención: {intent.name}
Entidades: {intent.entities}
Herramientas disponibles: {self._format_tools(shown_tools)}
Memoria relevante: {self._format_memories(context.relevant_memories)}
Pasos ya ejecutados (usa sus resultados con valores REALES, NO los repitas):
{done_text}
{repeat_warn}
Input original: {state['user_input']}

REGLAS DE SELECCION (obligatorias):
- Si el pedido dice "en segundo plano", "sin interrumpir", "fondo" o similar: prefiere discord_user_send (silenciosa) sobre discord_command (roba foco y teclado).
- job_start SOLO para tareas fire-and-forget (el usuario no espera el resultado ahora). Para esperas dentro de una secuencia, run_shell esta bien.
- discord_command SOLO cuando pidan escribir/tipear explicitamente o cuando un bot deba obedecer y no haya otra via.
- COMANDOS DISCORD (Jockie): SIEMPRE con m al principio: m!play/m!p, m!pause, m!resume, m!skip, m!queue, m!stop. PROHIBIDO mandar !play, !pause, !skip sin la m. Si dudas, agrega la m.
- CONTROL (pausa/reanuda/salta/frena): usa SIEMPRE la tool music_control (SOLO Discord, nunca toca YouTube/Spotify). PROHIBIDO mandar m!pause/m!resume/m!skip/m!stop a mano y PROHIBIDO buscar el control como tema.
- job_result solo despues de job_start/job_status, con un job_id REAL devuelto por una herramienta (nunca inventes IDs ni uses <placeholders>).
- ARTISTA RAMMA: se escribe SIEMPRE con doble M mayuscula (RAMMA). Si el input dice "rama" interpretalo como RAMMA. En music_play/music_search usa query con "RAMMA" y artist="RAMMA". Nunca busques "rama" a secas (eso trae al dios hindu u otra cosa).
- Musica (ORDEN OBLIGATORIO, tu cache primero): 1) si hay link, m!play directo; 2) si pide algo tuyo (tranqui/likeds/artista sin tema), mymusic_cached_play (instantaneo, salta repetidos) y con ese link arma m!play; 3) si hay texto puntual, mymusic_search y con ese link arma m!play; 4) SOLO si tu cuenta falla, music_play (YouTube general). music_search (yt-dlp generico) SOLO para mostrar opciones, nunca para reproducir.
- PLAYLISTS: "LA/MI playlist X" (ej 'pone la playlist pop') = TU biblioteca: mymusic_playlist_tracks con el link de memoria y m!play. "BUSCA/UNA playlist de X" (ej 'busca una playlist de Milo J', sin MI) = AFUERA: search_playlists (publicas, nunca tu biblioteca); si trae verbo de poner, m!play con la primera.
- DESTINO DEFAULT: poner/reproducir sin especificar donde = SIEMPRE Discord (m!p via discord_user_send). Nunca preguntes donde.
- WEB (web_search, intent separado, NUNCA fallback generico): solo para datos actuales que no estan en tu biblioteca (noticias, resultados, clima, precios). Query LIMPIA y corta (2-6 palabras, sin "busca en google/por favor"). La query incluye TODOS los datos que dio el usuario (equipos+evento+fecha: "final libertadores River Boca 2018", nunca "qué pasó Madrid" pelado). max_results 2-3. Si preguntan por HOY/ayer/esta semana SIN año pasado explícito, pasa time_range="week" ("day" si es resultado del dia); con año pasado (2018...) JAMAS pases time_range. Responde con el dato + fuente en 1-2 frases para voz. PROHIBIDO usar web_search para musica.
- Al MOSTRAR playlists (mymusic_playlists): nombra TODAS las del resultado con titulo exacto, sin recortar a 2-3.
- Si piden MI/MIS/MIA, biblioteca, gustos, historial, likeds, Tranqui o "algo mio / algo que me gusta": usa mymusic_playlists, mymusic_liked, mymusic_history o mymusic_playlist_tracks ANTES que cualquier busqueda generica. Para "poné mi Tranqui": mymusic_playlist_tracks con el link de memoria y m!play con un track no repetido.
- PROHIBIDO usar browser_open/browser_read para musica, salvo pedido explícito de buscar en el navegador: en ese caso extrae el link y termínalo con m!play. Opera GX NO se usa para scrapear tu biblioteca (para eso esta la API con tu cookie); el browser es solo para mostrar/abrir music.youtube.com si lo piden.
- PROHIBIDO asumir "Algun dia" por defecto: solo pon "Algun dia" si el usuario lo pidio explicito. Si piden RAMMA sin tema puntual, usa mymusic_search con query="RAMMA" y deja que la herramienta elija uno NO repetido. Si piden "esa"/"esa musica" sin contexto claro, usa el ultimo artista/tema del historial (RAMMA) en vez de inventar.
- Si piden "otra" / "otra cancion" / "otra de X": usa mymusic_search o music_play con el mismo artista o tema (salta solo lo ya reproducido).
- VERSIONES ("no/sin en vivo/acustico/remix/live", "video oficial"): NUNCA elijas un resultado con En Vivo/Live/Acustico/Remix/Cover en el titulo cuando lo pidieron evitar (ni Spanish si pidieron ingles). Si TODOS los resultados son la version prohibida, NO mandes m!play: avisa y ofrece buscar otro tema.
- Si piden QUITAR/sacar/cortar la actual Y poner otra ("quita esa y pone...", "saca esto y pasa..."): secuencia OBLIGATORIA en 2 pasos: 1) discord_user_send m!stop, 2) en el paso siguiente m!play <link nuevo>. PROHIBIDO mandar el tema nuevo sin frenar antes, y PROHIBIDO decir "Hecho" sin haber mandado ambos.
- Reproducir musica = SIEMPRE terminar enviando m!play <link> (nunca solo anunciar con texto).
- "No lo pongas / no lo hagas / solo decime / todavía no" = MOSTRAR con texto, PROHIBIDO m!play aunque haya verbo de poner.
- Correccion hacia vos ("mentira", "no lo hiciste", "te equivocaste") = responder de charla, JAMAS mandar nada a Discord.
- Si piden MI playlist por nombre: primero mymusic_playlists y usa la coincidencia de titulo; si no aparece (cookie vencida), usa el link guardado en memoria (Tranqui) con mymusic_playlist_tracks; solo si todo falla, music_search.
- PROHIBIDO repetir una herramienta con los mismos argumentos si ya devolvio ok:true: en ese caso responde {{"done": true}}.

BIBLIA DE ORDENES (archivo ordenes.md, se obedece literal, no se interpreta):
{orders}

Responde SOLO con un JSON: {{"tool": "tool_name", "args": {{...}}}} o {{"done": true}} si la tarea ya esta completa."""

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=state["user_input"]),
        ]

        try:
            # Plan con modelo rapido: el plan es JSON corto, no necesita el grande.
            # Reserva llm_main para chat/synthesize (donde se nota la calidad).
            response = await self.llm_fast.ainvoke(messages)
            raw = self._parse_plan(response.content)
            items = raw if isinstance(raw, list) else ([raw] if isinstance(raw, dict) else [])
            step = None
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get("done") is True or item.get("tool") in ("done", "finish", "none", "synthesize", None):
                    step = None
                    break
                if "tool" not in item:
                    continue
                args = item.get("args", {})
                if not isinstance(args, dict):
                    args = {}
                step = ActionStep(tool=item["tool"], args=args, depends_on=[], retry_policy={})
                break
            state["plan"] = [step] if step is not None else []
            if state["plan"]:
                keys = _ordered_step_keys(state["results"])
                last_ok = (
                    bool(keys)
                    and isinstance(state["results"].get(keys[-1]), dict)
                    and state["results"][keys[-1]].get("ok") is True
                )
                last_tool = (state.get("metadata") or {}).get("last_tool") or {}
                if (last_ok and _canon_step(last_tool.get("tool"), last_tool.get("args"))
                        == _canon_step(step.tool, step.args)):
                    logger.debug("paso_repetido_forzando_done", tool=step.tool)
                    state["plan"] = []
            if not state["plan"]:
                logger.debug("plan_vacio", output=str(response.content)[:200])
        except Exception as e:
            logger.error("planning_failed", error=str(e))
            state["plan"] = []

        await emit_event("on_plan_created", state["plan"])
        return state

    async def _execute(self, state: CLAUDIOState) -> CLAUDIOState:
        if not state["plan"]:
            return state

        step = state["plan"][0]
        # Blindaje final: aunque el LLM olvide la m o escriba "rama", lo corregimos aca.
        try:
            step.args = _canon_args(step.args)
        except Exception:
            pass
        tool_schema = self.plugin_registry.get_all_tools().get(step.tool)
        key = f"step_{len(_ordered_step_keys(state['results']))}"

        if not tool_schema:
            logger.error("tool_not_found", tool=step.tool)
            state["results"][key] = {"error": f"Tool {step.tool} not found"}
            return state

        await emit_event("on_tool_call", step.tool, step.args)

        try:
            plugin = self.plugin_registry.get_plugin_for_tool(step.tool)
            if plugin is None:
                result = {"error": f"Tool '{step.tool}' no disponible (plugin no instalado)"}
            else:
                result = await plugin.execute_tool(step.tool, step.args)

            state["results"][key] = result
        except Exception as e:
            logger.error("tool_execution_failed", tool=step.tool, error=str(e))
            state["results"][key] = {"error": str(e)}

        await emit_event("on_tool_result", step.tool, state["results"][key])
        state["metadata"]["last_tool"] = {"tool": step.tool, "args": step.args}
        hist = list((state.get("metadata") or {}).get("tool_history") or [])
        hist.append(step.tool)
        state["metadata"]["tool_history"] = hist[-12:]
        # Artista que suena: queda como "último" para "esa banda..." posterior.
        try:
            res = state["results"][key]
            if isinstance(res, dict) and res.get("ok") is True:
                artist = ""
                if step.tool == "music_play":
                    artist = (step.args.get("artist") or "").strip()
                elif step.tool == "mymusic_cached_play":
                    artist = (step.args.get("artist") or "").strip()
                    if not artist:
                        pick = res.get("pick")
                        if isinstance(pick, dict):
                            artist = str(pick.get("artists", "") or "")
                if artist:
                    remember_artist(artist)
        except Exception:
            pass
        # Registro de comandos enviados (para el automatismo m!stop/m!play,
        # el control multimedia y no mandar dos veces lo mismo).
        try:
            res = state["results"][key]
            if step.tool in ("discord_user_send", "discord_command", "music_control") \
                    and isinstance(res, dict) and res.get("ok") is True:
                txt = (step.args.get("message") or step.args.get("command") or "").strip()
                if step.tool == "music_control" and isinstance(res.get("command"), str):
                    txt = res["command"].strip()
                if txt:
                    sent = list((state.get("metadata") or {}).get("sent_commands") or [])
                    sent.append(txt)
                    state["metadata"]["sent_commands"] = sent[-6:]
        except Exception:
            pass
        return state

    async def _verify(self, state: CLAUDIOState) -> CLAUDIOState:
        keys = _ordered_step_keys(state["results"])
        if keys:
            last_result = state["results"].get(keys[-1], {})
            if isinstance(last_result, dict) and "error" in last_result:
                state["attempts"] = state.get("attempts", 0) + 1
        return state

    def _should_replan(self, state: CLAUDIOState) -> Literal["replan", "giveup"]:
        if len(_ordered_step_keys(state["results"])) >= MAX_TOTAL_STEPS:
            return "giveup"
        keys = _ordered_step_keys(state["results"])
        if keys:
            last_result = state["results"].get(keys[-1], {})
            if (isinstance(last_result, dict) and "error" in last_result
                    and state.get("attempts", 0) >= MAX_PLAN_RETRIES):
                return "giveup"
        return "replan"

    def _route_after_plan(self, state: CLAUDIOState) -> Literal["act", "done"]:
        return "act" if state["plan"] else "done"

    async def _synthesize(self, state: CLAUDIOState) -> CLAUDIOState:
        intent = state["intent"]
        results = state["results"]
        user_input = state["user_input"]
        show = "SI" if wants_show(user_input) else "NO"
        # Hora/fecha local ya resuelta por la tool: sin LLM.
        try:
            ans = _local_time_answer(user_input, results)
            if ans:
                state["response"] = ans
                await emit_event("on_response_synthesized", state["response"])
                return state
        except Exception:
            pass
        # Acción determinística ya hecha (m!p/m!pause/... con ok): confirmación
        # local instantánea, sin gastar otra llamada LLM (era la espera boba).
        if deterministic_done(state):
            hist = list((state.get("metadata") or {}).get("tool_history") or [])
            state["response"] = _local_confirm(results, hist, _sent_commands(state), user_input)
            await emit_event("on_response_synthesized", state["response"])
            return state
        history = await self._recent_history()
        hist = list((state.get("metadata") or {}).get("tool_history") or [])
        keys = _ordered_step_keys(results)
        shown = "\n".join(_format_step(k, results[k], hist, i) for i, k in enumerate(keys))

        system_prompt = f"""Genera una respuesta natural en {settings.claudio.response_language} para el usuario.
Eres CLAUDIO, el asistente de Señor. Los datos de memoria describen AL USUARIO, nunca a ti: jamas digas que te llamas Señor.
{PERSONA}
Respuestas cortas.
Señor es varón (masculino siempre); jamás insultes de vuelta.
Apodos (amigos/familia): SOLO los anotados en tu vault (usuario.md/ordenes.md), en tercera persona cuando el usuario los nombre. Al USUARIO nunca le pones apodo: a él, "señor".
PROHIBIDO responder en inglés u otro idioma: todo SIEMPRE en español rioplatense.
TODO corre en 2do plano y en silencio: confirma acciones con 1 palabra/frase mínima
(Pausado/Listo), SIN detallar pasos, herramientas ni links. SOLO si pide verlo
explícitamente ("mostrame", "decime qué hiciste", "qué está sonando") detalla.
Pidio verlo esta vez: {show}.
Tu versión real en ejecución: {_claudio_version()}.
Motor que genera ESTA respuesta: {getattr(self.llm_main, 'last_backend', None) or 'desconocido'}.
Si preguntan qué programa/modelo te genera o con qué motor andás, responde ese dato en 1 frase.
Fecha y hora actual: {_ahora_str()}.
Intención original: {intent.name if intent else 'unknown'}
Input: {user_input}
Conversacion reciente (para entender referencias como "eso", "de vuelta", "lo de antes"):
{history}
Memoria relevante del vault (usala si responde a la pregunta, si no, ignorala):
{self._format_memories(state["context"].relevant_memories)}
Resultados de herramientas (resumen, no cites su texto literal):
{shown if shown else "(sin resultados)"}
REGLA: si los resultados muestran ok:true, el trabajo YA esta hecho: informalo brevemente. No pidas datos ni credenciales para algo que ya se ejecuto con exito.
RESULTADOS DEPORTIVOS/NOTICIAS (fecha!): solo afirma un resultado si la fuente trae fecha de hoy/ayer; si el dato es viejo o sin fecha (ej. un video de otro partido), NO lo presentes como el de hoy: da el dato con su fecha o di que no encontraste el de hoy en 1 frase.
MARCADORES Y DATOS: copia ganador, perdedor y numeros EXACTOS como aparecen en los resultados ("River Plate 3-1 Boca" = ganó River 3 a 1). PROHIBIDO responder de memoria cuando hay resultados. Sin fuente, "no lo encontré" en 1 frase: no improvises ni inviertas datos.
Si lo mandado fue control (m!pause/resume/skip/stop/queue), confirma con UNA palabra: Pausado/Siguiendo/Siguiente/Frenado/Listo.
PROHIBIDO afirmar que hiciste algo si results esta vacio o solo tiene errores: en ese caso di la verdad (que no pudiste y por que) o pide el dato faltante.
Nunca repitas muletillas de YouTube ("gracias por ver el video", "suscribete", "dale like", links crudos): son basura de las paginas, no palabras tuyas.
Si la intencion es unknown, pide en una frase que aclare lo que necesita.

Respuesta concisa y útil:"""

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_input),
        ]

        try:
            response = await self.llm_main.ainvoke(messages)
            state["response"] = scrub_response(response.content)
            # Si terminó pidiendo aclaración, se guarda para retomar el tema
            # en el turno siguiente sin perder el hilo.
            if (intent is None or intent.name == "unknown") and "?" in state["response"]:
                _save_pending_question(state["response"])
        except Exception as e:
            logger.error("synthesis_failed", error=str(e))
            # Si el trabajo YA salió (ok:true en una ACCIÓN) pero el LLM de
            # respuesta murió (cuotas 429/timeouts), se confirma en local SIN
            # mentir ni pedir de nuevo. Preguntas sin acción -> se dice la verdad.
            state["response"] = _local_confirm(results, hist, _sent_commands(state), user_input)
        await emit_event("on_response_synthesized", state["response"])
        return state

    async def _speak(self, state: CLAUDIOState) -> CLAUDIOState:
        await emit_event("on_speak", state["response"])
        # La memoria (Chroma + extraccion de hechos) corre en fondo: no bloquea
        # el proximo turno. Igual que quick_reply.
        asyncio.create_task(self._safe_learn(state))
        return state

    async def _extract_facts(self, user_input: str, response: str) -> List[str]:
        from langchain_core.messages import HumanMessage, SystemMessage
        try:
            res = await self.llm_fast.ainvoke([
                SystemMessage(content=(
                    "De la conversacion, extrae hechos DURADEROS sobre el usuario "
                    "(datos, gustos, preferencias, proyectos, personas, compromisos) "
                    "como bullets cortos con '- '. Nada trivial ni temporal. Si no hay nada, responde VACIO.\n"
                    "REGLAS ANTI-RUIDO (obligatorias):\n"
                    "- El usuario se llama Señor: NUNCA digas que el usuario ES un artista "
                    "('Usuario es Paulo Londra/RAMMA/...' esta PROHIBIDO; lo correcto es 'le gusta...' si lo dijo).\n"
                    "- RAMMA va SIEMPRE con doble M. Si aparece 'rama', normalizalo a RAMMA.\n"
                    "- No guardes un tema puntual ('Algun dia', 'Billie Jean'...) como preferencia general "
                    "salvo que el usuario diga que es su favorito. Un pedido puntual NO es un gusto duradero.\n"
                    "- No guardes comandos Discord sin la M: siempre m!play/m!pause/m!resume/m!skip/m!queue/m!stop.\n"
                    "- No guardes basura de STT ('Lobertisatei', 'Robert Is Day de Cuco', 'liching chord', 'disco mm') "
                    "ni frases incompletas."
                )),
                HumanMessage(content=f"Usuario: {user_input}\nCLAUDIO: {response}"),
            ])
            facts = []
            for raw_line in str(res.content).splitlines():
                line = raw_line.strip().lstrip("-•* ").strip()
                if not line or line.upper() == "VACIO" or len(line) <= 10:
                    continue
                low = line.lower()
                # Filtros duros por si el LLM igual alucina:
                if low.startswith("usuario es ") and any(
                    a in low for a in ("paulo", "ramma", "rama", "cuco", "michael", "claudio")
                ):
                    continue
                if "cuco" in low and "ramma" not in low:
                    continue
                line = _RAMA_RE.sub("RAMMA", line)
                facts.append(line)
            return facts
        except Exception as e:
            logger.warning("extraccion_hechos_fallo", error=str(e)[:150])
            return []

    async def _learn(self, state: CLAUDIOState) -> CLAUDIOState:
        await self.memory.add_conversation(
            user_input=state["user_input"],
            response=state["response"],
            intent=state["intent"].name if state["intent"] else "unknown",
            metadata=state["metadata"],
        )
        # Órdenes explícitas ("guardalo en tu memoria"): se guardan LITERALES,
        # sin filtro de largo ni LLM de por medio.
        try:
            for fact in extract_memory_commands(state["user_input"]):
                self.memory.append_memory_fact(fact)
                # También guardar en ChromaDB para búsqueda semántica
                await self.memory.add_fact(fact, source="explicit_command", confidence=1.0)
        except Exception as e:
            logger.warning("learn_mando_fallo", error=str(e)[:150])
        # Hechos por LLM con freno: 1 cada 10 min como máximo (quema cuota).
        if len(state["user_input"]) + len(state["response"]) > 120 \
                and time.time() - self._last_facts_ts > 600:
            self._last_facts_ts = time.time()
            try:
                for fact in await self._extract_facts(state["user_input"], state["response"]):
                    self.memory.append_memory_fact(fact)
                    # También guardar en ChromaDB para búsqueda semántica
                    await self.memory.add_fact(fact, source="llm_extracted", confidence=0.8)
            except Exception as e:
                logger.warning("learn_hechos_fallo", error=str(e)[:150])
        
        # Guardar procedimiento exitoso si la intención fue exitosa
        if state.get("intent") and state["intent"].name != "unknown":
            try:
                success = bool(state.get("response") and len(state["response"]) > 10)
                await self.memory.add_procedural(
                    action=state["user_input"][:200],
                    context=f"intent={state['intent'].name}",
                    result=state["response"][:200] if state.get("response") else "",
                    success=success,
                )
            except Exception as e:
                logger.warning("learn_procedural_fallo", error=str(e)[:100])
        
        await emit_event("on_learned", state)
        return state

    def _format_intents(self, intents: Dict[str, Any]) -> str:
        return "\n".join([f"- {name}: {i.patterns}" for name, i in intents.items()])

    def _format_tools(self, tools: Dict[str, Any]) -> str:
        lines = []
        for name, t in tools.items():
            params = getattr(t, "parameters", {}) or {}
            parts = []
            for pname, p in params.items():
                req = "requerido" if getattr(p, "required", False) else "opcional"
                parts.append(f"{pname} ({getattr(p, 'type', 'string')}, {req}): {getattr(p, 'description', '')}")
            lines.append(f"- {name}: {t.description} | params: {'; '.join(parts) if parts else 'ninguno'}")
        return "\n".join(lines)

    async def process(self, user_input: str, thread_id: str = "default") -> str:
        # "Empezá de cero" = hilo nuevo: sin arrastre de checkpoints viejos.
        if wants_reset(user_input):
            self._thread_seq = getattr(self, "_thread_seq", 0) + 1
            thread_id = f"{thread_id}-nuevo{self._thread_seq}"
            user_input = _RESET_RE.sub(" ", user_input).strip() or "hola"
        initial_state: CLAUDIOState = {
            "user_input": user_input,
            "intent": None,
            "context": ConversationContext(),
            "plan": [],
            "attempts": 0,
            "results": {},
            "response": "",
            "metadata": {"thread_id": thread_id},
        }

        config = {"configurable": {"thread_id": thread_id}}
        final_state = await self.graph.ainvoke(initial_state, config)
        return final_state["response"]