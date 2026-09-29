import asyncio
import os
import queue
import re
import time
import unicodedata
from typing import List

import numpy as np

from core.voice import ToolNarrator, _sample_ambient, calibrate_threshold

EXIT_WORDS = {"salir", "terminar", "chau", "chao", "adios", "adiós", "corta", "cuelga", "colga"}

WAKE_VARIANTS = re.compile(
    r"\b(charly|charli|sharvis|jarviz|harvis|yarvis|garvis|jarbis|"
    r"charvis|charris|carris|yarris|carvis|karvis|dclaudio|zarvis|shari)\b"
)
_SPELLED_RE = re.compile(
    r"\b[a-z]\.(?:\s*[a-z]\.){1,}\s*[a-z]\.?(?![a-z])"
    r"|\b(?:[a-z]\s+){2,}[a-z]\b"
)
_SPELLED_OK = set("claudiochybgt")


def _spelled_to_claudio(m) -> str:
    letters = re.sub(r"[^a-z]", "", m.group(0).lower())
    # Whisper a veces pierde letras ("c.c.c.s.v." sin r): basta con 2+ letras
    # nucleo (j/a/r/v/i/s). f.b.i./u.s.a. quedan fuera por el set permitido.
    if (len(letters) >= 3 and set(letters) <= _SPELLED_OK
            and len(set(letters) & set("claudio")) >= 2):
        return "claudio"
    return m.group(0)


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def _extract_command(text: str):
    """Solo vale lo que viene DESPUES de 'claudio'. Lo anterior se ignora.

    Orden explicita de Señor: si hablaba de otra cosa (o sonaba musica) y
    despues dijo claudio, lo previo es basura y no entra al pedido.
    """
    low = _strip_accents((text or "").lower())
    low = _SPELLED_RE.sub(_spelled_to_claudio, low)
    low = WAKE_VARIANTS.sub("claudio", low)
    for wake in ("hey claudio", "claudio"):
        idx = low.find(wake)
        if idx != -1:
            after = low[idx + len(wake):].strip(" ,.!?¿¡")
            return True, " ".join(after.split())
    return False, text


def _skip_quiet_turn(wake_on: bool, wake_hit: bool, rec_time: float, misses: int) -> bool:
    """Turno corto sin 'claudio' detectado por Vosk: ni se transcribe.

    Vosk primero (barato, local); Whisper queda de red para turnos largos
    (Vosk puede errar 'Jarois') y para conversacion en curso (misses==0).
    """
    return bool(wake_on and not wake_hit and rec_time < 1.5 and misses > 0)


def _worth_fallback(text: str) -> bool:
    """El fallback 'cada 3 turnos igual se procesa' era lo que convertia
    alucinaciones ('Gracias.') en acciones. Solo vale si parece habla real:
    3+ palabras (un pedido corto tipo 'hola qué tal' pasa, 'Gracias.' no)."""
    return len(re.findall(r"[A-Za-zÁ-ÿñü]+", text or "")) >= 3


def _wake_gate(wake_ready: bool, wake_hit: bool, text: str, misses: int):
    """Decide si un turno se procesa. Devuelve (accion, texto, misses).
    accion: 'process' | 'ignore' | 'ask'."""
    try:
        from plugins.stt_whisper import is_hallucination_loop
        if is_hallucination_loop(text or ""):
            return ("ignore", text, misses)
    except Exception:
        pass
    if not (text or "").strip():
        return ("ignore", text, misses)
    if wake_ready and wake_hit:
        _, cleaned = _extract_command(text)
        return ("ask", cleaned, 0) if not cleaned else ("process", cleaned, 0)
    if wake_ready and not wake_hit:
        found, cleaned = _extract_command(text)
        if found:
            return ("process", cleaned, 0)
        if (misses + 1) % 3 == 0:
            if _worth_fallback(text):
                return ("process", text, 0)
            return ("ignore", text, misses + 1)
        return ("ignore", text, misses + 1)
    found, cleaned = _extract_command(text)
    if not found:
        return ("ignore", text, misses)
    return ("ask", cleaned, 0) if not cleaned else ("process", cleaned, 0)


class TurnDetector:
    """VAD por energia con histeresis: decide cuando empieza/termina un turno."""

    def __init__(self, threshold: float = 0.02, start_speech: float = 0.3, end_silence: float = 1.1):
        self.threshold = threshold
        self.start_speech = start_speech
        self.end_silence = end_silence
        self.reset()

    def reset(self) -> None:
        self.speaking = False
        self.speech_time = 0.0
        self.silence_time = 0.0

    def feed(self, rms: float, dt: float) -> str:
        loud = rms > self.threshold
        if not self.speaking:
            self.speech_time = self.speech_time + dt if loud else 0.0
            if self.speech_time >= self.start_speech - 1e-9:
                self.speaking = True
                self.silence_time = 0.0
                return "speaking"
            return "idle"
        if loud:
            self.silence_time = 0.0
        else:
            self.silence_time += dt
        if self.silence_time >= self.end_silence - 1e-9:
            self.speaking = False
            self.speech_time = 0.0
            return "turn_end"
        return "speaking"


def _rms(chunk: np.ndarray) -> float:
    return float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2)))


def _assemble(chunks: List[np.ndarray]) -> np.ndarray:
    audio = np.concatenate(chunks, axis=0).flatten().astype(np.float32)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 0:
        audio = audio / max(peak, 1e-6)
    try:
        from core.config import settings
        from core.voice import denoise_audio
        audio = denoise_audio(audio, sample_rate=settings.voice.sample_rate,
                              gate_db=settings.voice.noise_gate_db,
                              enabled=settings.voice.noise_suppress)
    except Exception:
        pass
    return (np.asarray(audio, dtype=np.float32) * 32767).astype(np.int16)


class CallLoop:
    def __init__(
        self,
        brain,
        stt,
        tts,
        sample_rate: int = 16000,
        threshold: float = 0.02,
        max_turn_seconds: float = 30.0,
        wake_enabled: bool = True,
        wake_sensitivity: float = 0.6,
    ):
        self.brain = brain
        self.stt = stt
        self.tts = tts
        self.sample_rate = sample_rate
        self.threshold = threshold
        self.max_turn_seconds = max_turn_seconds
        self.wake_enabled = wake_enabled
        self.wake_sensitivity = wake_sensitivity
        self._muted = False
        self._wake = None
        self._stopper = None
        self._mic_q = None

    async def _say(self, text: str) -> None:
        self._muted = True
        try:
            await self.tts.execute_tool("speak", {"text": text})
        except Exception as e:
            print(f"(TTS fallo: {e})")
        finally:
            await asyncio.sleep(0.4)
            self._muted = False

    async def _say_interruptible(self, text: str) -> str:
        """Habla por partes y corta al instante con 'para/alto/basta'.

        Devuelve "" si termino, o la palabra de corte escuchada.
        """
        from core.voice import _split_small, clean_for_speech
        self._muted = True
        try:
            try:
                import sounddevice as sd
            except Exception:
                sd = None
            try:
                parts = _split_small(clean_for_speech(text or ""))
            except Exception:
                parts = [text] if (text or "").strip() else []
            if not parts:
                return ""
            for part in parts:
                try:
                    r = await self.tts.execute_tool("speak", {"text": part, "play": False})
                except Exception as e:
                    print(f"(TTS fallo: {e})")
                    return ""
                path = r.get("file") if isinstance(r, dict) else None
                if not path:
                    continue
                try:
                    import soundfile as sf
                    playing = {}

                    def _play(_path=path, _playing=playing):
                        try:
                            data, sr = sf.read(_path, dtype="float32", always_2d=False)
                            _playing["done"] = False
                            sd.play(data, sr)
                            sd.wait()
                        finally:
                            _playing["done"] = True

                    task = asyncio.to_thread(_play)
                    task = asyncio.ensure_future(task)
                    while not task.done():
                        await asyncio.sleep(0.12)
                        word = await asyncio.to_thread(self._stop_heard)
                        if word:
                            try:
                                if sd is not None:
                                    sd.stop()
                            except Exception:
                                pass
                            try:
                                task.cancel()
                            except Exception:
                                pass
                            return word
                    word = await asyncio.to_thread(self._stop_heard)
                    if word:
                        return word
                finally:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
            return ""
        finally:
            await asyncio.sleep(0.3)
            self._muted = False

    def _init_wake(self):
        try:
            from core.wake import VoskSpotter
            w = VoskSpotter()
            w.start()
            print("(detección local Vosk activa: solo respondo si me llamás)")
            return w
        except Exception as e:
            print(f"(wake-word no disponible, escucho todo: {e})")
            return None

    def _init_stopper(self):
        """Detector local de 'para/alto/basta' para cortar el habla al instante.

        Reusa el modelo Vosk ya cargado (sin RAM extra). Devuelve None si no hay.
        """
        if self._stopper is not None:
            return self._stopper
        try:
            from core.wake import VoskSpotter
            model = getattr(self._wake, "_model", None)
            s = VoskSpotter(cooldown_seconds=1.2, words=("para", "alto", "basta"),
                            model=model)
            s.start()
            self._stopper = s
            return s
        except Exception:
            return None

    def _stop_heard(self) -> str:
        """Junta ~0.5s de mic (con compuerta de volumen) y busca orden de corte."""
        q = self._mic_q
        if q is None:
            return ""
        stopper = self._init_stopper()
        if stopper is None or not stopper.ready:
            return ""
        import numpy as np
        buf, loud, deadline = [], 0, time.time() + 0.5
        while time.time() < deadline:
            try:
                chunk = q.get(timeout=0.12)
            except queue.Empty:
                continue
            buf.append(chunk)
            rms = float(np.sqrt(np.mean(np.asarray(chunk, dtype=np.float32).astype(np.float64) ** 2)))
            loud = loud + 1 if rms > 0.08 else 0
            if loud >= 2:
                break
        if loud < 2 or not buf:
            return ""
        try:
            return stopper.hears(np.concatenate(buf, axis=0)) or ""
        except Exception:
            return ""

    async def _mic_interrupted(self, threshold: float = 0.08) -> bool:
        q = self._mic_q
        if q is None:
            return False
        while not q.empty():
            try:
                q.get_nowait()
            except queue.Empty:
                break
        loud, deadline = 0, time.time() + 0.4
        while time.time() < deadline:
            try:
                chunk = q.get(timeout=0.15)
            except queue.Empty:
                continue
            rms = float(np.sqrt(np.mean(np.asarray(chunk, dtype=np.float32).astype(np.float64) ** 2)))
            if rms > threshold:
                loud += 1
            else:
                loud = 0
            if loud >= 2:
                return True
        return False

    async def run(self) -> None:
        import sounddevice as sd

        q: "queue.Queue[np.ndarray]" = queue.Queue()

        def callback(indata, frames, t, status):
            q.put(indata.copy())

        print("Llamada iniciada. Deci 'claudio' + tu pedido ('salir' para cortar).")
        self._wake = self._init_wake()
        narrator = ToolNarrator(self._say)
        await narrator.start()
        try:
            with sd.InputStream(
                samplerate=self.sample_rate, channels=1, dtype="float32", callback=callback
            ):
                self._mic_q = q
                self.threshold = calibrate_threshold(
                    _sample_ambient(q), floor=0.015, ceil=0.06,
                )
                print(f"(mic calibrado: umbral {self.threshold:.3f})")
                misses = 0
                while True:
                    while not q.empty():
                        try:
                            q.get_nowait()
                        except queue.Empty:
                            break
                    detector = TurnDetector(threshold=self.threshold, end_silence=1.1)
                    frames: List[np.ndarray] = []
                    rec_time = 0.0
                    wake_hit = False
                    while True:
                        try:
                            chunk = q.get(timeout=0.2)
                            dur = len(chunk) / self.sample_rate
                        except queue.Empty:
                            if detector.feed(0.0, 0.2) == "turn_end":
                                break
                            continue
                        frames.append(chunk)
                        rec_time += dur
                        if self._wake is not None and self._wake.ready:
                            try:
                                if self._wake.feed(chunk):
                                    wake_hit = True
                            except Exception:
                                pass
                        if self._muted:
                            detector.reset()
                            frames.clear()
                            rec_time = 0.0
                            continue
                        if detector.feed(_rms(chunk), dur) == "turn_end":
                            break
                        if rec_time >= self.max_turn_seconds:
                            break
                    if rec_time < 0.5 or not frames:
                        continue
                    wake_on = self._wake is not None and self._wake.ready
                    if _skip_quiet_turn(wake_on, wake_hit, rec_time, misses):
                        print("(sin 'claudio', ignoro)")
                        continue
                    audio = _assemble(frames)
                    print("Transcribiendo...")
                    res = await asyncio.to_thread(self.stt.transcribe_array, audio)
                    text = (res.get("text") or "").strip()
                    if not text:
                        print("(no te escuche)")
                        continue
                    print(f"Tú: {text}")
                    if text.lower().rstrip(".,!?¡¿ ").strip() in EXIT_WORDS:
                        print("Cortando la llamada...")
                        break
                    wake_on = self._wake is not None and self._wake.ready
                    action, text, misses = _wake_gate(wake_on, wake_hit, text, misses)
                    if action == "ignore":
                        print("(sin 'claudio', ignoro)")
                        continue
                    if action == "ask":
                        await self._say("¿Sí?")
                        continue
                    try:
                        route = self.brain._route_after_perceive({"user_input": text})
                    except Exception:
                        route = "full"

                    interrupted = {"skip": False}

                    async def speak_now(sentence: str, _state=interrupted) -> None:
                        if _state["skip"]:
                            return
                        await self._say(sentence)
                        try:
                            if await self._mic_interrupted():
                                print("(interrumpido, te escucho)")
                                _state["skip"] = True
                        except Exception:
                            pass

                    try:
                        if route == "chat":
                            await self.brain.quick_reply(text, on_sentence=speak_now)
                        else:
                            response = await self.brain.process(text)
                            cut = await self._say_interruptible(response)
                            if cut:
                                print(f"(cortaste con '{cut}', ejecutando el corte)")
                                try:
                                    resp = await self.brain.process("para")
                                    print(f"CLAUDIO: {resp}")
                                except Exception as e:
                                    print(f"(error: {e})")
                            elif interrupted.get("skip"):
                                print("(interrumpido, te escucho)")
                    except Exception as e:
                        print(f"(error: {e})")
        except KeyboardInterrupt:
            print("\nLlamada cortada.")
        finally:
            await narrator.stop()
            if self._wake is not None:
                self._wake.stop()
                self._wake = None
