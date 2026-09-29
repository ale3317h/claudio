import os
import tempfile
from typing import Any, Dict, List

from dotenv import load_dotenv

from plugins.base import IntentPattern, Permission, PluginBase, PluginManifest, ToolParameter, ToolSchema

load_dotenv()

FALLBACK_VOICES = ["es-MX-JorgeNeural", "es-ES-AlvaroNeural"]


from core.voice import clean_for_speech as _clean


class Plugin(PluginBase):
    manifest = PluginManifest(
        name="tts_edge",
        version="1.0.0",
        description="Texto a voz neuronal con Microsoft Edge",
        author="CLAUDIO",
        category="audio",
        dependencies=[],
        permissions=[Permission.AUDIO, Permission.NETWORK],
    )

    async def initialize(self, core) -> None:
        from core.config import settings
        self.voice = settings.voice.tts_voice
        self.rate = settings.voice.tts_rate
        self.pitch = getattr(settings.voice, "tts_pitch", "-15Hz")
        self.core = core

    async def shutdown(self) -> None:
        pass

    def _cache_dir(self):
        import pathlib
        d = pathlib.Path(__file__).resolve().parent.parent.parent / "data" / "tts_cache"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _cache_path(self, cleaned: str, voice: str, rate: str, pitch: str = ""):
        import hashlib
        key = hashlib.sha256(f"{voice}|{rate}|{pitch}|{cleaned}".encode()).hexdigest()[:16]  # noqa: S324
        return self._cache_dir() / f"{key}.mp3"

    def _prune_cache(self, keep: int = 50) -> None:
        try:
            files = sorted(self._cache_dir().glob("*.mp3"),
                           key=lambda p: p.stat().st_mtime)
            for old in files[:-keep] if len(files) > keep else []:
                try:
                    old.unlink()
                except OSError:
                    pass
        except Exception:
            pass

    async def tool_speak(self, text: str, voice: str = "", rate: str = "", pitch: str = "", play: bool = True) -> Dict[str, Any]:
        import edge_tts
        cleaned = _clean(text)
        if not cleaned:
            return {"error": "Texto vacio"}
        # Frases cortas fijas ("Listo.", "Pausado."): audio cacheado, instantáneo.
        # Se sirve COPIA (los llamantes borran el archivo tras usarlo).
        v0, r0 = voice or self.voice, rate or self.rate
        p0 = pitch or getattr(self, "pitch", "-15Hz")
        if len(cleaned) <= 60:
            try:
                import shutil
                hit = self._cache_path(cleaned, v0, r0, p0)
                if hit.exists() and hit.stat().st_size > 0:
                    fd, tmp = tempfile.mkstemp(suffix=".mp3")
                    os.close(fd)
                    shutil.copyfile(str(hit), tmp)
                    if play:
                        import sounddevice as sd
                        import soundfile as sf
                        try:
                            data, sr = sf.read(tmp, dtype="float32", always_2d=False)
                            sd.play(data, sr)
                            sd.wait()
                        finally:
                            try:
                                os.remove(tmp)
                            except OSError:
                                pass
                        return {"ok": True, "chars": len(cleaned), "cached": True}
                    return {"ok": True, "chars": len(cleaned), "file": tmp,
                            "cached": True}
            except Exception:
                pass
        voices = [v0] + [v for v in FALLBACK_VOICES if v != v0]
        last_err = ""
        path = ""
        for v in voices:
            try:
                fd, path = tempfile.mkstemp(suffix=".mp3")
                os.close(fd)
                # OJO: edge-tts 7.2.8 NO acepta SSML (escapa el texto como plano
                # y el servicio LEE las etiquetas). Prosodia por parametros.
                await edge_tts.Communicate(cleaned, v, rate=r0, pitch=p0).save(path)
                break
            except Exception as e:
                last_err = str(e)
                path = ""
        if not path:
            return {"error": f"TTS fallo: {last_err[:200]}"}
        if len(cleaned) <= 60:
            try:
                import shutil
                dest = self._cache_path(cleaned, v0, r0, p0)
                shutil.copyfile(path, str(dest))
                self._prune_cache()
                if not play:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                    return {"ok": True, "chars": len(cleaned), "file": str(dest)}
            except Exception:
                pass
        if play:
            try:
                import sounddevice as sd
                import soundfile as sf
                data, sr = sf.read(path, dtype="float32", always_2d=False)
                sd.play(data, sr)
                sd.wait()
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass
            return {"ok": True, "chars": len(cleaned)}
        return {"ok": True, "chars": len(cleaned), "file": path}

    def get_tools(self) -> List[ToolSchema]:
        return [
            ToolSchema(
                name="speak",
                description="Convertir texto a voz y reproducirlo por los parlantes.",
                parameters={
                    "text": ToolParameter(type="string", description="Texto a hablar", required=True),
                    "voice": ToolParameter(type="string", description="Voz (por defecto la configurada)"),
                    "rate": ToolParameter(type="string", description="Velocidad, ej: -5%"),
                    "pitch": ToolParameter(type="string", description="Tono, ej: -15Hz (más grave estilo CLAUDIO)"),
                    "play": ToolParameter(type="boolean", description="Reproducir (false = solo generar archivo)"),
                },
            ),
        ]

    def get_intents(self) -> List[IntentPattern]:
        return []
