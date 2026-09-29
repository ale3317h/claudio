from collections import deque


class WakeDebouncer:
    def __init__(self, required_hits: int = 2, window_seconds: float = 1.5,
                 cooldown_seconds: float = 5.0):
        self.required_hits = max(1, required_hits)
        self.window = window_seconds
        self.cooldown = cooldown_seconds
        self._hits: deque = deque()
        self._last_fire = float("-inf")

    def feed(self, hit: bool, now: float) -> bool:
        if hit:
            self._hits.append(now)
        while self._hits and now - self._hits[0] > self.window:
            self._hits.popleft()
        if len(self._hits) >= self.required_hits and now - self._last_fire >= self.cooldown:
            self._last_fire = now
            self._hits.clear()
            return True
        return False


class VoskSpotter:
    def __init__(self, model_dir=None, cooldown_seconds: float = 4.0,
                 words=("claudio",), model=None):
        self.model_dir = model_dir
        self.words = tuple(w.lower() for w in words)
        self._model = model
        self._rec = None
        self._debouncer = WakeDebouncer(1, 2.0, cooldown_seconds)

    @property
    def ready(self) -> bool:
        return self._rec is not None

    def start(self) -> None:
        if self.model_dir is None:
            from core.config import settings
            self.model_dir = str(
                settings.memory.vault_path / "data" / "vosk-model" / "vosk-model-small-es-0.42"
            )
        import json as _json
        import os
        if self._model is None:
            if not os.path.isdir(self.model_dir):
                raise RuntimeError(f"Falta modelo Vosk en {self.model_dir}")
            from vosk import KaldiRecognizer, Model, SetLogLevel
            SetLogLevel(-1)
            self._model = Model(self.model_dir)
        else:
            from vosk import KaldiRecognizer
        grammar = _json.dumps([*self.words, "[unk]"], ensure_ascii=False)
        self._rec = KaldiRecognizer(self._model, 16000, grammar)

    def feed(self, chunk_f32) -> bool:
        return self.hears(chunk_f32) is not None

    def hears(self, chunk_f32):
        """Devuelve la palabra detectada (de `words`) o None. Reusable para 'para'/'alto'."""
        import json
        import time

        import numpy as np
        if self._rec is None:
            return None
        pcm = (np.asarray(chunk_f32, dtype=np.float32).flatten() * 32767).astype(np.int16)
        try:
            if not self._rec.AcceptWaveform(pcm.tobytes()):
                return None
            text = json.loads(self._rec.Result()).get("text", "").lower()
        except Exception:
            return None
        for w in self.words:
            if w in text:
                if self._debouncer.feed(True, time.time()):
                    return w
                return None
        self._debouncer.feed(False, time.time())
        return None

    def stop(self) -> None:
        self._rec = None
        self._model = None
