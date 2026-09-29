import asyncio
import os
from typing import Any, Dict, List, Optional

import numpy as np
import structlog
from dotenv import load_dotenv

from plugins.base import IntentPattern, Permission, PluginBase, PluginManifest, ToolParameter, ToolSchema

load_dotenv()

logger = structlog.get_logger(__name__)

GROQ_STT_MODEL = "whisper-large-v3-turbo"
GROQ_STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

# Prompt con vocabulario propio para que Whisper no invente.
# RAMMA va con doble M (no "rama"), y los comandos Discord van con m! (no "!play" solo).
STT_PROMPT = (
    "Conversación casual en español rioplatense con Señor y CLAUDIO. "
    "El asistente se llama CLAUDIO (una palabra, nunca deletreado por letras). "
    "Nombres: RAMMA (cantante argentino de trap, se escribe con doble M), "
    "Ramiro, Soda Stereo, Pablo Londra. "
    "Comandos de Discord: m!play, m!p, m!pause, m!resume, m!skip, m!queue, m!stop. "
    # NOTA: "Saja Boys" NO va en este prompt. Estuvo aca y Whisper (temperature=0,
    # deterministico) lo repetia como eco ante cualquier audio debil/musica de fondo:
    # todo salia "Saja B". Los casos reales ("saga/aga boys") los cubre el mapeo
    # post-STT en normalize/canon (no necesitan el prompt para transcribirse).
    "Mas vocabulario: Jockie (el bot), "
    "Gonna Fly Now (tema de Rocky), skippea/esquipear (saltar tema). "
    "Temas de RAMMA: En una, Cuando pase la tormenta, Algún día, Otro beso, Nostalgia."
)

# El STT suele devolver "rama" (una m) aunque el usuario dijo RAMMA,
# y "!play" sin la m aunque el comando real es "m!play". Se corrige abajo.
import re as _re

# El STT escucha "reanudar" como "renovar/renueva": siempre es reanudar acá.
_REANUDA_RE = _re.compile(r"\brenov[ao][a-z]*\b|\brenuev[ao][a-z]*\b", _re.IGNORECASE)


_CLAUDIO_TYPO_RE = _re.compile(
    r"\b(charvis|c[aá]rvis|karvis|dclaudio|zarvis|yarvis|harvis|garvis|jarviz|jarbis|sharvis|charris|carris|yarris|shari|sharis)\b",
    _re.IGNORECASE,
)
# Whisper a veces devuelve "Claudio" deletreado: "C.R.V.S." / "C C R V S".
# El lookahead final evita comer la inicial de la palabra siguiente ("C.R.V.S. me"!).
_SPELLED_RE = _re.compile(
    r"\b[A-Za-z]\.(?:\s*[A-Za-z]\.){1,}\s*[A-Za-z]\.?(?![A-Za-z])"
    r"|\b(?:[A-Z]\s+){2,}[A-Z]\b"
)
# Letras que aparecen cuando deletrean mal CLAUDIO (C<->J, Y/H/G...). F.B.I. no entra.
_SPELLED_OK = set("CLAUDIOCHYBGT")


def _spelled_to_claudio(m) -> str:
    letters = _re.sub(r"[^A-Za-z]", "", m.group(0)).upper()
    # Whisper a veces pierde letras ("C.C.C.S.V." sin R): basta con 2+ letras
    # nucleo (J/A/R/V/I/S). F.B.I./U.S.A. quedan fuera por el set permitido.
    if (len(letters) >= 3 and set(letters) <= _SPELLED_OK
            and len(set(letters) & set("CLAUDIO")) >= 2):
        return "CLAUDIO"
    return m.group(0)


_WAKE_STRIP_RE = _re.compile(
    r"\b(charly|charli|sharvis|jarviz|harvis|yarvis|garvis|jarbis|"
    r"charvis|charris|carris|yarris|carvis|karvis|dclaudio|zarvis|claudio|shari)\b",
    _re.IGNORECASE,
)


_FALSE_START_RE = _re.compile(
    r"^(?:(?:par[áa]|alto|basta|eh+|mmm+|este+|che|bueno|perd[oó]n|o\s+sea)\b[,\s]*)*"
    r"no\b\s*,\s*(?=\S)",
    _re.IGNORECASE,
)


def strip_false_start(text: str) -> str:
    """Saca el arranque fallido ('pará, no, abrí Chrome' -> 'abrí Chrome')."""
    t = text or ""
    return _FALSE_START_RE.sub("", t).strip() or t


def strip_before_wake(text: str) -> str:
    """Todo lo anterior al último 'Claudio' se ignora (balbuceo previo)."""
    t = text or ""
    last = None
    for m in _WAKE_STRIP_RE.finditer(t):
        last = m
    if last is None:
        return t
    return t[last.start():].lstrip(" ,.!?¿¡-") or t


# El STT también lo escucha como "Carby/Carbi/Ramba": todo es RAMMA en este contexto.
_RAMA_RE = _re.compile(r"\bram+a\b|\bcarb[yi]\b|\bramba\b", _re.IGNORECASE)
_SKILLET_RE = _re.compile(
    r"\bskijet\b|\bskyjet\b|\bskyshet\b|\bskilet\b|\bskillets?\b"
    r"|\bsquilet\b|\besquilet\b|\beskillet\b|\bskidget\b|\bskidjet\b"
    r"|\bkitschets?\b|\bkitshets?\b|\bkischets?\b",
    _re.IGNORECASE,
)
# "Raze/Raice" = Rise (el tema de Skillet que siempre pide).
_RISE_RE = _re.compile(r"\braze\b|\braice\b", _re.IGNORECASE)
_JOIN_HYPHEN_RE = _re.compile(r"\b(?:[A-Za-z]\s*[-–]\s*){2,}[A-Za-z]\b")


def _join_spelled_word(m) -> str:
    return _re.sub(r"[\s\-–]", "", m.group(0)).upper()
# "Poblodondra/Pablo Londra" (y variantes) = Paulo Londra, su artista #1.
_PAULO_RE = _re.compile(
    r"\bpa[bu]l[ao]\s+londra\b|\bpobl[ao]\s+londr?a\b"
    r"|\bpobl[ao]dondra\b|\bpoblolondra\b|\bpobl[ao]\s+(?:de\s+)?londra\b",
    _re.IGNORECASE,
)
# Alucinaciones tipicas de Whisper en silencios (nunca son del usuario).
# Medido: en silencio/ruido Groq devuelve "Gracias." con no_speech_prob=0.000
# (el endpoint no informa confianza real) y avg_logprob igual al habla.
# Por eso se filtra por texto, no por score del modelo.
_YT_SPAM_RE = _re.compile(
    r"\bgracias por ver (este |el )?video\b|\bno olvides suscribirte\b"
    r"|\bsuscrib[ei]te\b|\bsuscribirse\b|\bdale (like|me gusta)\b"
    r"|\bdeja tu like\b|\bhasta el pr[óo]ximo video\b"
    r"|\bnos vemos en el pr[óo]ximo video\b"
    r"|\bsubt[íi]tulos?\s+(realizados?|creados?|por)\b",
    _re.IGNORECASE,
)
# "Gracias." suelto = alucinacion de silencio casi siempre. Con mas palabras
# ("gracias por poner eso") puede ser real y se conserva.
_SOLO_GRACIAS_RE = _re.compile(r"^gracias[.!]?$", _re.IGNORECASE)
# Señor NUNCA dice "disco": siempre es Discord (el STT lo confunde).
# Orden explicita: todo "disco" suelto se corrige a Discord.
# "Liscord" es Discord con la L del articulo pegada ("el Discord" -> "Liscord").
_DISCO_RE = _re.compile(r"\bdisco\b|\bliscord\b", _re.IGNORECASE)


def is_hallucination_loop(text: str) -> bool:
    """Detecta alucinaciones de Whisper: loops repetidos en silencio o con
    musica de fondo (letras del parlante). Ej: 'Algun dia, Otro beso,
    Nostalgia. Tema, Algun dia, Otro beso...' o relied reiterados.
    El habla real casi nunca repite un 4-grama ni tiene <40% palabras unicas.
    """
    words = _re.sub(r"[^\wáéíóúñü]", " ", (text or "").lower()).split()
    n = len(words)
    if n < 12:
        return False
    if len(set(words)) / n < 0.4:
        return True
    from collections import Counter
    if Counter(words).most_common(1)[0][1] >= 6:
        return True
    grams = Counter(tuple(words[i:i + 4]) for i in range(n - 3))
    return bool(grams) and grams.most_common(1)[0][1] >= 2


# "!play ..." / "¡play ..." sin la m -> "m!play ..."
_BARE_BANG_CMD_RE = _re.compile(
    r"(^|[\s\"'¿¡(\[])[!¡]+(play|p|pause|resume|unpause|skip|queue|stop|clear)\b",
    _re.IGNORECASE,
)
# "eme play", "em play" (error típico de STT por "m!play") -> "m!play"
_EME_PLAY_RE = _re.compile(
    r"\b(em+e|en)\s+(!?(play|p|pause|resume|skip|queue|stop))\b",
    _re.IGNORECASE,
)
# "m play" / "mplay ..." sin signo -> "m!play ..."
_M_SPACE_CMD_RE = _re.compile(
    r"\bm\s+(play|p|pause|resume|skip|queue|stop)\b",
    _re.IGNORECASE,
)


def fix_transcript(text: str) -> str:
    """Normaliza errores sistemáticos del STT sin tocar el resto.

    - rama/ramaa -> RAMMA (el artista; pedido explícito de Señor)
    - disco -> Discord (Señor nunca dice 'disco'; orden explícita)
    - !play/!pause/... sin m -> m!play/m!pause/...
    - loops alucinados (silencio/musica de fondo) -> "" (se ignoran)
    """
    t = text or ""
    if not t.strip():
        return t
    t = _DISCO_RE.sub("Discord", t)
    t = _SPELLED_RE.sub(_spelled_to_claudio, t)
    t = _CLAUDIO_TYPO_RE.sub("CLAUDIO", t)
    t = _REANUDA_RE.sub("reanudar", t)
    t = strip_before_wake(t)
    t = strip_false_start(t)
    t = _JOIN_HYPHEN_RE.sub(_join_spelled_word, t)
    # Tartamudeo ("re-re-re-anuda") y cumbia villera ("vichero" = villero).
    t = _re.sub(r"\b(\w{1,3})(?:-\1){1,}-?", r"\1", t)
    t = _re.sub(r"\b([A-Za-z]{1,2})[-–](?=[A-Za-z])", r"\1", t)
    t = _re.sub(r"\b(vichero|vichera|viyero|viyera|biyero|biyera|villegru|vilegru)\b", "villero", t,
                flags=_re.IGNORECASE)
    t = _re.sub(r"\bpauz[aoá]\b", "pausa", t, flags=_re.IGNORECASE)
    t = _re.sub(r"\bsil\b", "sin", t, flags=_re.IGNORECASE)
    t = _RAMA_RE.sub("RAMMA", t)
    t = _PAULO_RE.sub("Paulo Londra", t)
    t = _SKILLET_RE.sub("Skillet", t)
    t = _RISE_RE.sub("Rise", t)
    t = _YT_SPAM_RE.sub(" ", t)
    t = " ".join(t.split())
    t = _EME_PLAY_RE.sub(lambda m: f"m!{m.group(2).lstrip('!')}", t)
    t = _BARE_BANG_CMD_RE.sub(lambda m: f"{m.group(1)}m!{m.group(2)}", t)
    t = _M_SPACE_CMD_RE.sub(lambda m: f"m!{m.group(1)}", t)
    if _SOLO_GRACIAS_RE.match(t.strip()):
        return ""
    if is_hallucination_loop(t):
        return ""
    return t


def _to_wav_bytes(audio_int16: np.ndarray, sample_rate: int = 16000) -> bytes:
    import io
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(np.asarray(audio_int16, dtype=np.int16).tobytes())
    return buf.getvalue()


class Plugin(PluginBase):
    manifest = PluginManifest(
        name="stt_whisper",
        version="1.1.0",
        description="Voz a texto: Groq Whisper (rapido) con fallback local faster-whisper",
        author="CLAUDIO",
        category="audio",
        dependencies=[],
        permissions=[Permission.AUDIO],
    )

    async def initialize(self, core) -> None:
        from core.config import settings
        self.backend = (settings.voice.stt_backend or "local").lower()
        self.model_name = settings.voice.stt_model
        self.device = settings.voice.stt_device
        self.compute = settings.voice.stt_compute_type
        lang = (settings.claudio.language or "es").lower()
        self.language = {"es": "es", "en": "en"}.get(lang)
        self._model = None
        self.core = core

    async def shutdown(self) -> None:
        self._model = None

    def _get_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(self.model_name, device=self.device, compute_type=self.compute)
        return self._model

    def _transcribe_groq(self, pcm: np.ndarray) -> Dict[str, Any]:
        import httpx
        api_key = os.getenv("GROQ_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("sin GROQ_API_KEY")
        try:
            from core.config import settings
            groq_model = (settings.voice.stt_groq_model or "").strip() or GROQ_STT_MODEL
        except Exception:
            groq_model = GROQ_STT_MODEL
        wav = _to_wav_bytes(pcm)
        with httpx.Client(timeout=60) as client:
            r = client.post(
                GROQ_STT_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": ("audio.wav", wav, "audio/wav")},
                data={"model": groq_model, "language": "es", "response_format": "json",
                       "temperature": "0",
                       "prompt": STT_PROMPT},
            )
        r.raise_for_status()
        text = fix_transcript((r.json().get("text") or "").strip())
        return {"text": text, "language": "es", "duration": round(len(pcm) / 16000, 2)}

    def _transcribe_local(self, pcm: np.ndarray) -> Dict[str, Any]:
        data = pcm.astype(np.float32) / 32768.0
        model = self._get_model()
        segments, info = model.transcribe(
            data, language=self.language, beam_size=5, vad_filter=True,
            initial_prompt=STT_PROMPT,
        )
        text = fix_transcript(" ".join(s.text.strip() for s in segments).strip())
        return {"text": text, "language": info.language, "duration": float(info.duration or 0.0)}

    def transcribe_array(self, audio: np.ndarray, language: Optional[str] = None) -> Dict[str, Any]:
        arr = np.asarray(audio).flatten()
        if arr.size == 0:
            return {"text": "", "language": "", "duration": 0.0}
        if arr.dtype == np.int16:
            pcm = arr
        else:
            f = arr.astype(np.float32)
            peak = float(np.max(np.abs(f)))
            f = f / max(peak, 1e-6) * 0.9 if peak > 0 else f
            pcm = (f * 32767).astype(np.int16)
        # Puerta de silencio: audio normalizado a pico pero sin energia
        # sostenida (ruido/click) no va al STT: ahorra la llamada y evita
        # alucinaciones en callado. Habla real sostiene energia por silabas.
        try:
            if pcm.size and float(np.mean(np.abs(pcm) > 2000)) < 0.002:
                return {"text": "", "language": "", "duration": 0.0}
        except Exception:
            pass
        if self.backend == "groq":
            try:
                return self._transcribe_groq(pcm)
            except Exception as e:
                logger.warning("stt_groq_fallo_usando_local", error=str(e))
        return self._transcribe_local(pcm)

    async def tool_transcribe(self, audio_path: str, language: str = "") -> Dict[str, Any]:
        import soundfile as sf
        data, sr = sf.read(audio_path, dtype="float32", always_2d=False)
        if sr != 16000:
            return {"error": f"Se esperaba audio a 16000 Hz, llego a {sr} Hz"}
        return await asyncio.to_thread(self.transcribe_array, np.asarray(data), language or None)

    def get_tools(self) -> List[ToolSchema]:
        return [
            ToolSchema(
                name="transcribe",
                description="Transcribir un archivo de audio (16 kHz mono) a texto.",
                parameters={
                    "audio_path": ToolParameter(type="string", description="Ruta del archivo de audio", required=True),
                    "language": ToolParameter(type="string", description="Idioma (es, en; vacio = auto)"),
                },
            ),
        ]

    def get_intents(self) -> List[IntentPattern]:
        return []
