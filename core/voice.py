import asyncio
import os
import queue
import re
from typing import List, Optional

import numpy as np

from core.events import event_bus

SLOW_NARRATIONS = {
    "music_search": "Buscando {query}...",
    "browser_open": "Abriendo {url}...",
    "browser_read": "Leyendo la página...",
    "transcribe": "Transcribiendo...",
    "web_search": "Dame un segundo que busco eso...",
}


def narration_for(tool: str, args) -> str | None:
    tmpl = SLOW_NARRATIONS.get(tool or "")
    if not tmpl:
        return None
    try:
        safe = {k: str(v)[:60] for k, v in (args or {}).items()}
        return tmpl.format(**safe)
    except Exception:
        return tmpl.split("{")[0].strip() or None


def _mic_device_name() -> str:
    """Nombre del mic por defecto: si dice Voicemod y esta cerrado, no entra nada."""
    try:
        import sounddevice as sd
        idx = sd.default.device[0]
        return str(sd.query_devices(idx)["name"])
    except Exception:
        return "desconocido"


def _is_silence(peak: float, floor: float = 0.02) -> bool:
    """Pico bajo ~-34dBFS = silencio digital (mic muteado/bloqueado/equivocado).

    Normalizar eso lo amplifica a full escala y el STT alucina ("Más de Racky"
    de la nada). Se descarta antes.
    """
    return float(peak or 0.0) < floor


def _mic_report(peak: float, seconds: float) -> str:
    """Diagnóstico de una línea: si el pico es bajísimo, el STT no tiene
    de dónde agarrarse (mic lejos/bajo o ganancia baja) y alucina."""
    import math
    db = 20.0 * math.log10(max(float(peak or 0.0), 1e-9))
    base = f"(audio: {seconds:.1f}s, pico {db:.0f} dBFS)"
    if db < -30:
        return base + " MIC MUY BAJO: habla mas cerca/fuerte o subi ganancia"
    return base


def _save_last_mic(path, pcm_int16, sample_rate: int) -> None:
    """Guarda lo que escuchó el mic para oírlo después (data/last_mic.wav)."""
    try:
        import wave
        p = str(path)
        with wave.open(p, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(int(sample_rate))
            w.writeframes(np.asarray(pcm_int16, dtype=np.int16).tobytes())
    except Exception:
        pass


def _stop_speakers() -> None:
    """Corta cualquier audio en reproduccion.

    Si el usuario aprieta PAUSE mientras CLAUDIO habla (o suena musica),
    el micro grabaria esa salida y el STT la transcribiria en loop.
    """
    try:
        import sounddevice as sd
        sd.stop()
    except Exception:
        pass


STUCK_REPEATS = 3  # mismo transcript N veces seguidas = STT trabado, no el usuario


def _stuck_update(prev_text: str, prev_count: int, text: str):
    """Detector de STT trabado.

    Con temperature=0 Whisper es deterministico: musica de fondo o la propia
    voz saliendo del parlante devuelven siempre el mismo texto ('Saja B').
    Devuelve (trabado, nuevo_prev, nuevo_count).
    """
    t = " ".join((text or "").split()).lower()
    p = " ".join((prev_text or "").split()).lower()
    if not t:
        return False, "", 0
    if t == p:
        n = prev_count + 1
        if n >= STUCK_REPEATS:
            return True, t, 0
        return False, t, n
    return False, t, 1


class ToolNarrator:
    def __init__(self, speak):
        self._speak = speak
        self._handler = None

    async def start(self) -> None:
        async def _on_tool(tool, args):
            text = narration_for(tool, args)
            if text:
                try:
                    await self._speak(text)
                except Exception:
                    pass

        self._handler = _on_tool
        event_bus.subscribe("on_tool_call", self._handler)

    async def stop(self) -> None:
        if self._handler is not None:
            try:
                event_bus.unsubscribe("on_tool_call", self._handler)
            except Exception:
                pass
            self._handler = None


_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF"
    "\u2B00-\u2BFF\uFE0F\u200D\u2640-\u2642\u2690-\u2691\U0001F1E6-\U0001F1FF]+"
)


def clean_for_speech(text: str, max_chars: int = 1000) -> str:
    text = re.sub(r"```.*?```", " ", text or "", flags=re.DOTALL)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = _URL_RE.sub("", text)
    text = _EMOJI_RE.sub("", text)
    # Glitches del LLM que se leerian mal ("Quése" -> "Qué se").
    text = re.sub(r"\b([Qq])u[eé]se\b", r"\1ué se", text)
    text = re.sub(r"\b([Cc])onozcola\b", r"\1onozco la", text)
    for ch in ("*", "#", "`", "_", ">", "|"):
        text = text.replace(ch, "")
    return re.sub(r"\s+", " ", text).strip()[:max_chars]


def _split_small(text: str, max_chars: int = 180) -> List[str]:
    parts = re.split(r"(?<=[.!?…])\s+", (text or "").strip())
    chunks, buf = [], ""
    for p in parts:
        if buf and len(buf) + len(p) + 1 > max_chars:
            chunks.append(buf.strip())
            buf = p
        else:
            buf = (buf + " " + p).strip()
    if buf.strip():
        chunks.append(buf.strip())
    out = []
    for chunk in chunks:
        rest = chunk
        while len(rest) > max_chars:
            cut = rest.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars
            out.append(rest[:cut].strip())
            rest = rest[cut:].strip()
        if rest:
            out.append(rest)
    return out


def calibrate_threshold(chunks, floor: float = 0.015, ceil: float = 0.06, factor: float = 4.0) -> float:
    import numpy as np
    if not chunks:
        return 0.02
    data = np.concatenate([np.asarray(c, dtype=np.float32).flatten() for c in chunks])
    base = float(np.sqrt(np.mean(data.astype(np.float64) ** 2))) if data.size else 0.0
    return min(ceil, max(floor, base * factor))


def _sample_ambient(q, seconds: float = 0.4) -> list:
    import time
    out, end = [], time.time() + seconds
    while time.time() < end:
        try:
            out.append(q.get(timeout=0.1))
        except queue.Empty:
            pass
    return out


VK_PTT = 0x90  # default (Bloq Num): se pisa con voice.ptt_key si esta configurado
VK_ESCAPE = 0x1B

# Candidatas push-to-talk: nombre -> (codigo virtual, nombre para carteles).
PTT_KEYS = {
    "pause": (0x13, "PAUSE"),
    "numlock": (0x90, "BLOQ NUM"),
    "bloqnum": (0x90, "BLOQ NUM"),
    "scroll": (0x91, "SCROLL"),
    "insert": (0x2D, "INSERT"),
    "rctrl": (0xA3, "CTRL DER"),
    "apps": (0x5D, "MENU"),
}


def _resolve_ptt_key(spec) -> tuple:
    """Nombre/hex de config -> (codigo, nombre). Desconocido = default."""
    s = str(spec or "").strip().lower()
    if s in PTT_KEYS:
        return PTT_KEYS[s]
    if s.startswith("0x"):
        try:
            return (int(s, 16), s.upper())
        except ValueError:
            pass
    return (VK_PTT, "BLOQ NUM")


def _key_down(vk: int) -> bool:
    """¿Tecla física presionada AHORA? Sin dependencias (GetAsyncKeyState)."""
    try:
        import ctypes
        return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
    except Exception:
        return False


def denoise_audio(audio_float, sample_rate: int = 16000, gate_db: float = -38.0,
                  enabled: bool = True):
    """Supresion de ruido liviana estilo Discord (sin dependencias nuevas).

    1) Quita DC + pasa-altos ~100Hz (ronquido/hum de fondo).
    2) Compuerta descendente por tramas de 20ms: lo que esta bajo el umbral
       se atenua a 12% en vez de cortarse de golpe (no come inicios de palabra).
    Entra float32 -1..1, sale igual. Con enabled=False devuelve intacto.
    """
    import numpy as np
    x = np.asarray(audio_float, dtype=np.float32).flatten()
    if not enabled or x.size == 0:
        return x
    x = x - float(np.mean(x))
    try:
        from scipy.signal import lfilter
        alpha = float(np.exp(-2.0 * np.pi * 100.0 / float(sample_rate)))
        x = lfilter([1.0, -1.0], [1.0, -alpha], x).astype(np.float32)
    except Exception:
        pass
    frame = max(1, int(sample_rate * 0.02))
    nfr = (x.size + frame - 1) // frame
    pad = nfr * frame - x.size
    if pad:
        x = np.concatenate([x, np.zeros(pad, dtype=np.float32)])
    fr = x.reshape(nfr, frame)
    rms = np.sqrt(np.mean(fr.astype(np.float64) ** 2, axis=1) + 1e-12)
    thr = 10.0 ** (gate_db / 20.0)
    target = np.where(rms > thr, 1.0, 0.12).astype(np.float32)
    # Suavizado ataque/relax para no clicar: media movil de ~5 tramas.
    k = min(5, nfr)
    sm = np.convolve(target, np.ones(k) / k, mode="same")
    gain = np.repeat(sm, frame)[:x.size - (pad or 0)] if pad else np.repeat(sm, frame)
    return (x[:x.size - (pad or 0)] * gain).astype(np.float32)


async def _synth_file(tts, text):
    try:
        r = await tts.execute_tool("speak", {"text": text, "play": False})
    except Exception:
        return None
    if isinstance(r, dict):
        return r.get("file")
    return None


async def _play_file(path: str) -> None:
    import sounddevice as sd
    import soundfile as sf

    def _play():
        data, sr = sf.read(path, dtype="float32", always_2d=False)
        sd.play(data, sr)
        sd.wait()

    await asyncio.to_thread(_play)


def _drop(task) -> None:
    if task is None:
        return
    if not task.done():
        task.cancel()
    else:
        try:
            task.result()
        except Exception:
            pass


async def speak_fluent(tts, text: str, mic_check=None) -> bool:
    parts = _split_small(text)
    if not parts:
        return True
    pending = None
    try:
        for i, part in enumerate(parts):
            if pending is None:
                fname = await _synth_file(tts, part)
            else:
                try:
                    fname = await pending
                except asyncio.CancelledError:
                    raise
                except Exception:
                    fname = None
            pending = None
            if fname is None:
                continue
            if i + 1 < len(parts):
                pending = asyncio.create_task(_synth_file(tts, parts[i + 1]))
            try:
                await _play_file(fname)
            finally:
                try:
                    os.remove(fname)
                except OSError:
                    pass
            if mic_check is not None:
                try:
                    if await mic_check():
                        return False
                except Exception:
                    pass
        return True
    finally:
        _drop(pending)


async def sample_mic_loud(threshold: float = 0.08, seconds: float = 0.35) -> bool:
    import numpy as np
    import sounddevice as sd

    def _read():
        with sd.InputStream(samplerate=16000, channels=1, dtype="float32") as stream:
            frames, _ = stream.read(int(16000 * seconds))
            return np.asarray(frames, dtype=np.float32)

    try:
        data = await asyncio.to_thread(_read)
    except Exception:
        return False
    n = max(1, len(data) // 3)
    loud = 0
    for i in range(3):
        seg = data[i * n:(i + 1) * n]
        if seg.size and float(np.sqrt(np.mean(seg.astype(np.float64) ** 2))) > threshold:
            loud += 1
    return loud >= 2


def _split_sentences(text: str, max_chars: int = 400) -> List[str]:
    parts = re.split(r"(?<=[.!?…])\s+", (text or "").strip())
    chunks, buf = [], ""
    for p in parts:
        if buf and len(buf) + len(p) + 1 > max_chars:
            chunks.append(buf.strip())
            buf = p
        else:
            buf = (buf + " " + p).strip()
    if buf.strip():
        chunks.append(buf.strip())
    return chunks or [text]


class VoiceLoop:
    def __init__(
        self,
        brain,
        stt,
        tts,
        sample_rate: int = 16000,
        threshold: float = 0.02,
        silence_seconds: float = 1.2,
        max_record_seconds: float = 30.0,
        listen_timeout: float = 10.0,
    ):
        self.brain = brain
        self.stt = stt
        self.tts = tts
        self.sample_rate = sample_rate
        self.threshold = threshold
        self.silence_seconds = silence_seconds
        self.max_record_seconds = max_record_seconds
        self.listen_timeout = listen_timeout
        try:
            from core.config import settings
            self.vk_ptt, self.ptt_name = _resolve_ptt_key(settings.voice.ptt_key)
        except Exception:
            self.vk_ptt, self.ptt_name = VK_PTT, "BLOQ NUM"

    def _wait_press(self) -> str:
        """Bloquea hasta la tecla PTT (hablar) o ESC (salir). Corre en thread."""
        import time as _t
        while True:
            if _key_down(VK_ESCAPE):
                return "esc"
            if _key_down(self.vk_ptt):
                _t.sleep(0.05)
                if _key_down(self.vk_ptt):
                    _stop_speakers()
                    return "ptt"
            _t.sleep(0.02)

    def _record_once(self) -> Optional[np.ndarray]:
        """Graba MIENTRAS la tecla PTT esta presionada. Al soltar, se envia.

        Sin VAD ni timeouts de escucha: mandas vos con el dedo.
        """
        import sounddevice as sd

        q: "queue.Queue[np.ndarray]" = queue.Queue()

        def callback(indata, frames, t, status):
            q.put(indata.copy())

        with sd.InputStream(
            samplerate=self.sample_rate, channels=1, dtype="float32", callback=callback
        ):
            self.threshold = calibrate_threshold(_sample_ambient(q, seconds=0.25))
            print(f"Hablando... (soltá {self.ptt_name} para enviar)")
            rec: List[np.ndarray] = []
            recorded = 0.0
            up = 0
            while True:
                try:
                    chunk = q.get(timeout=0.1)
                    rec.append(chunk)
                    recorded += len(chunk) / self.sample_rate
                except queue.Empty:
                    pass
                if _key_down(self.vk_ptt):
                    up = 0
                else:
                    up += 1
                    if up >= 2:
                        break
                if recorded >= self.max_record_seconds:
                    print("(límite de grabación)")
                    break

        if not rec or recorded < 0.4:
            print(f"(muy corto, mantené {self.ptt_name} y hablá)")
            return None
        audio = np.concatenate(rec, axis=0).flatten().astype(np.float32)
        peak = float(np.max(np.abs(audio)))
        if _is_silence(peak):
            print("(no te escuché: el mic llega en silencio, revisá ganancia/dispositivo)")
            return None
        if peak > 0:
            audio = audio / max(peak, 1e-6)
        try:
            from core.config import settings
            audio = denoise_audio(audio, sample_rate=self.sample_rate,
                                   gate_db=settings.voice.noise_gate_db,
                                   enabled=settings.voice.noise_suppress)
        except Exception:
            pass
        pcm = (np.asarray(audio, dtype=np.float32) * 32767).astype(np.int16)
        print(_mic_report(peak, recorded))
        try:
            from core.config import settings
            _save_last_mic(settings.memory.vault_path / "data" / "last_mic.wav",
                           pcm, self.sample_rate)
        except Exception:
            pass
        return pcm

    async def run(self) -> None:
        print(f"Modo voz: mantené {self.ptt_name} para hablar, soltá para enviar, ESC para salir.")
        print(f"Micrófono: {_mic_device_name()}")

        async def _speak(text: str) -> None:
            await self.tts.execute_tool("speak", {"text": text})

        narrator = ToolNarrator(_speak)
        await narrator.start()
        try:
            await self._run_loop()
        finally:
            await narrator.stop()

    async def _run_loop(self) -> None:
        prev_text, repeat = "", 0
        while True:
            key = await asyncio.to_thread(self._wait_press)
            if key == "esc":
                break
            audio = await asyncio.to_thread(self._record_once)
            if audio is None:
                continue
            print("Transcribiendo...")
            res = await asyncio.to_thread(self.stt.transcribe_array, audio)
            text = (res.get("text") or "").strip()
            if not text:
                print("No entendi nada, intenta de nuevo.")
                prev_text, repeat = "", 0
                continue
            print(f"Tú (voz): {text}")
            stuck, prev_text, repeat = _stuck_update(prev_text, repeat, text)
            if stuck:
                # El STT devuelve siempre lo mismo: no es el usuario, es musica
                # de fondo o la propia voz del parlante. No se procesa de nuevo.
                print(f"(loop cortado: '{text}' {STUCK_REPEATS}x seguidas)")
                try:
                    await self.tts.execute_tool("speak", {"text": (
                        "Señor, todo me llega igual. Si hay música de fondo, "
                        "o si aprieta la tecla mientras hablo, me escucho a mí mismo.")})
                except Exception:
                    pass
                continue
            try:
                route = self.brain._route_after_perceive({"user_input": text})
            except Exception:
                route = "full"
            if route == "chat":
                # Via rapida: 1 llamada LLM + habla por frases (streaming).
                async def speak_now(sentence: str) -> None:
                    try:
                        await self.tts.execute_tool("speak", {"text": sentence})
                    except Exception as e:
                        print(f"(TTS fallo: {e})")

                try:
                    await self.brain.quick_reply(text, on_sentence=speak_now)
                except Exception as e:
                    print(f"(error: {e})")
                continue
            try:
                response = await self.brain.process(text)
            except Exception as e:
                response = f"Tuve un problema: {e}"
            interrupted = await speak_fluent(
                self.tts, response,
                mic_check=sample_mic_loud,
            )
            if interrupted is False:
                print("(interrumpido, te escucho)")
