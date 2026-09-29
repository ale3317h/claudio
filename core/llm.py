import asyncio
import os
import re
import time
from datetime import UTC

import structlog
from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_ollama import ChatOllama

from core.config import settings

load_dotenv()

logger = structlog.get_logger(__name__)

DEFAULT_ATTEMPT_TIMEOUT = 12.0  # failover rapido: si un backend tarda >12s, pasa al siguiente
DEFAULT_TIMEOUTS = {"ollama": 30.0}  # local tibio responde en 4-10s; 60s colgaba el turno

# Bloques de razonamiento que algunos modelos meten en el texto. Se recortan siempre.
_THINK_RE = re.compile(
    r"<(think|thinking|analysis|reasoning|thought|reason)[^>]*>.*?</\1>",
    re.DOTALL | re.IGNORECASE,
)


def _midnight_utc_ts() -> float:
    from datetime import datetime, timedelta
    now = datetime.now(UTC)
    nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return nxt.timestamp()


def _retry_after_seconds(message: str, default: float = 600.0) -> float:
    m = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", message or "")
    if m:
        try:
            return int(m.group(1) or 0) * 60 + float(m.group(2))
        except (TypeError, ValueError):
            pass
    return default


def _short_error(exc: Exception) -> str:
    s = str(exc).replace("\n", " ").strip()
    if "429" in s:
        return "cuota agotada (429)"
    if "404" in s or "NOT_FOUND" in s:
        return "modelo no disponible (404)"
    if "401" in s or "Unauthorized" in s or "invalid" in s.lower():
        return "credencial invalida (401)"
    return s[:100]


class ResilientChatModel:
    """Cadena de backends con salud persistente: salta los sin cuota."""

    def __init__(self, chain, label: str = "", store=None, hedge_after_s: float = 2.0):
        norm = []
        for i, item in enumerate(chain or []):
            if isinstance(item, (tuple, list)) and len(item) == 2:
                norm.append((str(item[0]), item[1]))
            elif item is not None:
                norm.append((f"model-{i}", item))
        if not norm:
            raise RuntimeError("Sin backends LLM disponibles")
        self.chain = norm
        self.label = label
        self.store = store
        self.timeouts = dict(DEFAULT_TIMEOUTS)
        self.last_backend: str | None = None
        self.warmup_errors: list = []
        # Latencia EMA por backend (solo memoria de proceso): el router sabe
        # de antemano cual viene rapido sin gastar llamadas para averiguarlo.
        self._lat_ema: dict = {}
        # Si el primario no responde en este tiempo, se prende el siguiente
        # EN PARALELO y gana el primero que termina (hedged request).
        self.hedge_after_s = float(hedge_after_s)

    def _timeout_for(self, name: str) -> float:
        return float(self.timeouts.get(name, DEFAULT_ATTEMPT_TIMEOUT))

    @staticmethod
    def _normalize(resp):
        content = getattr(resp, "content", None)
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, str) and block.strip():
                    parts.append(block.strip())
                elif (isinstance(block, dict) and block.get("type") == "text"
                        and block.get("text")):
                    parts.append(str(block["text"]).strip())
            try:
                resp.content = " ".join(p for p in parts if p)
            except Exception:
                pass
        # Algunos backends de respaldo (razonadores) meten su chain-of-thought
        # en el texto (<think>...</think>). Eso nunca debe llegar al usuario/TTS.
        # OJO: sin .strip() — en streaming cada chunk es un delta (" todo") y
        # pelar bordes ahi borra los espacios entre palabras (bug real 2026-09-16).
        try:
            if isinstance(getattr(resp, "content", None), str) and resp.content:
                resp.content = _THINK_RE.sub("", resp.content)
        except Exception:
            pass
        return resp

    def _usable(self, name: str) -> bool:
        if self.store is None:
            return True
        try:
            st = self.store.get_backend_state(name)
        except Exception:
            return True
        if not st:
            return True
        if st.get("status") == "disabled":
            return False
        return not (st.get("status") == "limited" and (st.get("until") or 0) > time.time())

    def _is_disabled(self, name: str) -> bool:
        try:
            st = self.store.get_backend_state(name) if self.store else None
        except Exception:
            return False
        return bool(st) and st.get("status") == "disabled"

    def _note_lat(self, name: str, ms: float) -> None:
        try:
            old = self._lat_ema.get(name)
            self._lat_ema[name] = float(ms) if old is None else 0.5 * old + 0.5 * float(ms)
        except Exception:
            pass

    def _ranked_candidates(self):
        """Cadena ordenada: sanos primero (por latencia EMA), el resto despues.

        Los `disabled` (401) van ultimos: solo se prueban si no queda otra.
        Estable: respeta el orden de config en empates.
        """
        def key(item):
            name, _ = item
            usable = self._usable(name)
            ema = self._lat_ema.get(name, 1500.0)
            return (0 if usable else 1, 1 if self._is_disabled(name) else 0,
                    min(ema, 10000.0))
        cands = sorted(self.chain, key=key)
        non_disabled = [c for c in cands if not self._is_disabled(c[0])]
        return non_disabled or cands

    def _mark_ok(self, name: str) -> None:
        self.last_backend = name
        if self.store is None:
            return
        try:
            self.store.set_backend_state(name, "ok", 0.0)
        except Exception:
            pass

    def _mark_fail(self, name: str, exc: Exception) -> None:
        msg = str(exc)
        now = time.time()
        if "429" in msg or "rate_limit" in msg.lower():
            if "per day" in msg or "TPD" in msg or "RPD" in msg or "daily" in msg.lower():
                status, until = "limited", _midnight_utc_ts()
            else:
                status, until = "limited", now + _retry_after_seconds(msg)
        elif "401" in msg or "Unauthorized" in msg:
            status, until = "disabled", 0.0  # credencial mala: no reintentar hasta cambiar la key
        elif "404" in msg or "NOT_FOUND" in msg:
            # Modelo retirado o nombre mal escrito: castigo temporal, NO permanente.
            # (Un disabled latching dejo a CLAUDIO mudo aunque el resto se recuperara.)
            status, until = "limited", now + 600.0
        else:
            status, until = "limited", now + 60.0
        if self.store is not None:
            try:
                self.store.set_backend_state(name, status, until)
            except Exception:
                pass
        logger.warning("llm_backend_marcado", backend=name, estado=status, error=_short_error(exc))

    async def warmup(self, per_backend_timeout: float = 8.0) -> str:
        """Prueba en vivo con un micro-prompt y devuelve el backend ganador.

        Descarta keys muertas/quotas agotadas ANTES de la primera conversacion
        (gasta ~15 tokens una sola vez por arranque). '' si ninguno responde.
        Los `disabled` (401) se prueban ULTIMOS: si la key se arreglo en .env,
        reviven solos; si sigue muerta, falla rapido sin gastar cuota.
        """
        from langchain_core.messages import HumanMessage
        probe = [HumanMessage(content="Responde con una sola palabra: OK")]
        self.warmup_errors = []
        ordered = [c for c in self.chain if self._usable(c[0])]
        ordered += [c for c in self.chain
                    if not self._usable(c[0]) and self._is_disabled(c[0])]
        for name, model in ordered:
            # Ollama en frio tarda ~30s (carga 5GB a CPU): darle su timeout largo.
            timeout = max(per_backend_timeout, self._timeout_for(name))
            try:
                await asyncio.wait_for(model.ainvoke(probe), timeout=timeout)
                self._mark_ok(name)
                self._log(name, probe, None, True)
                return name
            except Exception as e:
                self._mark_fail(name, e)
                self.warmup_errors.append((name, _short_error(e)))
        return ""

    async def healthcheck(self, per_backend_timeout: float = 8.0):
        """Verifica TODOS los backends en paralelo. Devuelve [(name, ok, ms, err)].

        No frena en el primero sano: es la radiografía completa al arrancar.
        Hasta los `disabled` (401) se reprueban una vez por arranque por si la
        key se arreglo en .env (el 401 falla rapido y no gasta cuota).
        Gasta ~15 tokens por backend sano, una vez por arranque.
        """
        from langchain_core.messages import HumanMessage
        probe = [HumanMessage(content="Responde con una sola palabra: OK")]
        self.warmup_errors = []

        async def _one(name, model):
            t0 = time.time()
            try:
                timeout = max(per_backend_timeout, self._timeout_for(name))
                await asyncio.wait_for(model.ainvoke(probe), timeout=timeout)
                ms = int((time.time() - t0) * 1000)
                self._mark_ok(name)
                self._log(name, probe, None, True)
                return (name, True, ms, "")
            except Exception as e:
                ms = int((time.time() - t0) * 1000)
                self._mark_fail(name, e)
                short = _short_error(e)
                self.warmup_errors.append((name, short))
                return (name, False, ms, short)

        return list(await asyncio.gather(
            *[_one(n, m) for n, m in self.chain]))

    def _log(self, name: str, input, resp=None, ok: bool = True, err: str = "") -> None:
        if self.store is None:
            return
        try:
            out = "" if resp is None else str(getattr(resp, "content", resp) or "")
            self.store.log_llm_call(name, len(str(input)), len(out), ok, err[:120])
        except Exception:
            pass

    async def _each(self):
        return list(self.chain)

    async def _attempt(self, name: str, model, input, config, kwargs):
        """Un intento con timeout. Devuelve ("ok", name, resp) o ("err", name, exc)."""
        t0 = time.monotonic()
        try:
            resp = await asyncio.wait_for(
                model.ainvoke(input, config, **kwargs), timeout=self._timeout_for(name))
            dt_ms = (time.monotonic() - t0) * 1000.0
            self._note_lat(name, dt_ms)
            self._mark_ok(name)
            self._log(name, input, resp, True)
            logger.debug("llm_backend_ok", backend=name, ms=int(dt_ms))
            return ("ok", name, self._normalize(resp))
        except TimeoutError:
            err = TimeoutError(f"timeout {self._timeout_for(name)}s en {name}")
            self._mark_fail(name, err)
            self._log(name, input, None, False, str(err))
            return ("err", name, err)
        except Exception as e:
            self._mark_fail(name, e)
            self._log(name, input, None, False, str(e))
            return ("err", name, e)

    @staticmethod
    async def _wait_done(tasks, timeout_s=None, poll_s: float = 0.02):
        """Espera tasks como asyncio.wait, pero con polling.

        NO usa asyncio.wait(timeout): en el ProactorEventLoop de Windows este
        duerme el timeout completo aunque haya tasks listas (verificado
        2026-09-17). Con polling de 20ms la deteccion es inmediata.
        """
        tasks = set(tasks or [])
        if not tasks:
            return set()
        t0 = time.monotonic()
        while True:
            done = {t for t in tasks if t.done()}
            if done:
                return done
            if timeout_s is not None and time.monotonic() - t0 >= timeout_s:
                return set()
            await asyncio.sleep(poll_s)

    @staticmethod
    def _reap(tasks) -> None:
        for u in tasks:
            try:
                u.exception()
            except BaseException:
                pass

    async def ainvoke(self, input, config=None, **kwargs):
        cands = self._ranked_candidates()
        if not cands:
            raise RuntimeError("Sin backends LLM disponibles")
        for name, _ in cands:
            if not self._usable(name):
                logger.debug("llm_backend_saltado_sin_cuota", backend=name)
        tasks: dict = {}

        async def launch(name, model):
            tasks[name] = asyncio.ensure_future(
                self._attempt(name, model, input, config, kwargs))

        byname = dict(cands)
        order = [n for n, _ in cands]
        await launch(order[0], byname[order[0]])
        idx = 1
        hedge = max(0.2, float(self.hedge_after_s or 0))
        last = None
        while True:
            done = await self._wait_done(set(tasks.values()), timeout_s=hedge)
            if not done:
                if idx < len(order):
                    # El primario no responde en hedge_s: backup en paralelo,
                    # gana el primero que termina (no se esperan los 12s).
                    logger.debug("llm_hedge_disparado", lento=order[idx - 1],
                                 backup=order[idx])
                    await launch(order[idx], byname[order[idx]])
                    idx += 1
                    continue
                if not tasks:
                    break
                done = await self._wait_done(set(tasks.values()), timeout_s=None)
            for t in done:
                kind, name, payload = t.result()
                tasks.pop(name, None)
                if kind == "ok":
                    for u in tasks.values():
                        u.cancel()
                    self._reap(list(tasks.values()))
                    return payload
                last = payload
        raise last if last is not None else RuntimeError("Sin backends LLM disponibles")

    def invoke(self, input, config=None, **kwargs):
        last = None
        for name, model in self._ranked_candidates():
            if not self._usable(name):
                continue
            try:
                resp = model.invoke(input, config, **kwargs)
                self._mark_ok(name)
                return self._normalize(resp)
            except Exception as e:
                last = e
                self._mark_fail(name, e)
        raise last if last is not None else RuntimeError("Sin backends LLM disponibles")

    async def astream(self, input, config=None, **kwargs):
        usable = self._ranked_candidates()
        first, rest = usable[0], usable[1:]
        try:
            async for chunk in first[1].astream(input, config, **kwargs):
                yield self._normalize(chunk)
            self._mark_ok(first[0])
            return
        except Exception as e:
            self._mark_fail(first[0], e)
        for name, model in rest:
            try:
                response = await model.ainvoke(input, config, **kwargs)
                self._mark_ok(name)
                yield self._normalize(response)
                return
            except Exception as e:
                self._mark_fail(name, e)
        raise RuntimeError("Todos los backends LLM fallaron")


def _groq_keys() -> list:
    plural = os.getenv("GROQ_API_KEYS", "").strip()
    if plural:
        keys = [k.strip() for k in plural.split(",") if k.strip()]
        if keys:
            return keys
    single = os.getenv("GROQ_API_KEY", "").strip()
    return [single] if single else []


def _build_groq(role: str, api_key: str = ""):
    key = (api_key or os.getenv("GROQ_API_KEY", "")).strip()
    if not key:
        raise RuntimeError("Falta GROQ_API_KEY")
    model = settings.core.groq_model_main if role == "main" else settings.core.groq_model_fast
    temperature = settings.core.temperature if role == "main" else 0.1
    max_tokens = 640 if role == "main" else 512
    logger.info("llm_backend", backend="groq", model=model, role=role)
    return ChatGroq(model=model, temperature=temperature, max_tokens=max_tokens, api_key=key)


def _build_gemini(role: str):
    from langchain_google_genai import ChatGoogleGenerativeAI
    api_key = os.getenv("GOOGLE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Falta GOOGLE_API_KEY")
    model = settings.core.gemini_model_main if role == "main" else settings.core.gemini_model_fast
    temperature = settings.core.temperature if role == "main" else 0.1
    logger.info("llm_backend", backend="gemini", model=model, role=role)
    return ChatGoogleGenerativeAI(model=model, temperature=temperature, google_api_key=api_key)


def _build_ollama(role: str):
    model = settings.core.model_main if role == "main" else settings.core.model_fast
    logger.info("llm_backend", backend="ollama", model=model, role=role)
    return ChatOllama(
        model=model,
        temperature=settings.core.temperature if role == "main" else 0.1,
        num_ctx=settings.core.max_context_tokens,
        keep_alive="5m",  # libera RAM/VRAM a los 5 min sin uso
    )


def _build_openrouter(role: str):
    from langchain_openai import ChatOpenAI
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Falta OPENROUTER_API_KEY")
    model = settings.core.openrouter_model_main if role == "main" else settings.core.openrouter_model_fast
    temperature = settings.core.temperature if role == "main" else 0.1
    max_tokens = 640 if role == "main" else 512
    logger.info("llm_backend", backend="openrouter", model=model, role=role)
    return ChatOpenAI(model=model, temperature=temperature, max_tokens=max_tokens,
                      api_key=api_key, base_url="https://openrouter.ai/api/v1")


def _build_github(role: str):
    from langchain_openai import ChatOpenAI
    api_key = os.getenv("GITHUB_PAT", "").strip()
    if not api_key:
        raise RuntimeError("Falta GITHUB_PAT")
    model = settings.core.github_model_main if role == "main" else settings.core.github_model_fast
    temperature = settings.core.temperature if role == "main" else 0.1
    max_tokens = 640 if role == "main" else 512
    logger.info("llm_backend", backend="github", model=model, role=role)
    return ChatOpenAI(model=model, temperature=temperature, max_tokens=max_tokens,
                      api_key=api_key, base_url="https://models.github.ai/inference")


def _build_dream(role: str):
    from langchain_openai import ChatOpenAI
    api_key = os.getenv("DREAMPROMPTING_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Falta DREAMPROMPTING_API_KEY")
    model = settings.core.dream_model_main if role == "main" else settings.core.dream_model_fast
    temperature = settings.core.temperature if role == "main" else 0.1
    max_tokens = 640 if role == "main" else 512
    logger.info("llm_backend", backend="dream", model=model, role=role)
    return ChatOpenAI(model=model, temperature=temperature, max_tokens=max_tokens,
                      api_key=api_key, base_url="https://dreamprompting.com/api/v1")


def _build_meta(role: str):
    """Muse Spark via Meta Model API (OpenAI-compatible, 1M contexto).

    Modelo de razonamiento: va mejor con temperature 1.0 (docs) y SIN tope
    chico de max_tokens (el razonamiento consume presupuesto).
    Key: MODEL_API_KEY en .env (https://dev.meta.ai/api-keys).
    """
    from langchain_openai import ChatOpenAI
    api_key = os.getenv("MODEL_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Falta MODEL_API_KEY")
    model = settings.core.meta_model_main if role == "main" else settings.core.meta_model_fast
    logger.info("llm_backend", backend="meta", model=model, role=role)
    return ChatOpenAI(model=model, temperature=1.0, max_tokens=2048,
                      api_key=api_key, base_url="https://api.meta.ai/v1")


_BUILDERS = {"groq": _build_groq, "gemini": _build_gemini, "openrouter": _build_openrouter,
             "dream": _build_dream, "github": _build_github, "meta": _build_meta,
             "ollama": _build_ollama}


def _get_store():
    try:
        from core.store import Store
        return Store(settings.memory.vault_path / "data")
    except Exception:
        return None


def reorder_by_usage(chain, store):
    """Ordena la cadena por gasto real de hoy: la key/backend menos usada primero.

    Groq no expone API de saldo, asi que se usa el uso registrado en DB
    (llm_usage) + salud: con errores hoy va al fondo. Estable: respeta el
    orden de config en empates (el proveedor preferido sigue primero).
    """
    try:
        usage = store.llm_usage_today() if store is not None else {}
    except Exception:
        return chain
    if not usage:
        return chain

    def score(item):
        name, _ = item
        u = usage.get(name, {}) or {}
        return (1 if (u.get("errors") or 0) > 0 else 0, u.get("tokens") or 0)

    return sorted(chain, key=score)


def build_chat_model(role: str = "main"):
    backend = (settings.core.backend or "ollama").lower()
    order = [backend] + [b.lower() for b in (settings.core.fallbacks or []) if b.lower() != backend]
    store = _get_store()
    chain = []
    for name in order:
        if name == "groq":
            keys = _groq_keys()
            if not keys:
                logger.warning("llm_backend_no_disponible", backend=name, error="sin keys")
                continue
            for i, key in enumerate(keys):
                entry = "groq" if i == 0 else f"groq{i + 1}"
                try:
                    chain.append((entry, _build_groq(role, key)))
                except Exception as e:
                    logger.warning("llm_backend_no_disponible", backend=entry, error=str(e)[:150])
            continue
        builder = _BUILDERS.get(name)
        if builder is None:
            continue
        try:
            chain.append((name, builder(role)))
        except Exception as e:
            logger.warning("llm_backend_no_disponible", backend=name, error=str(e)[:150])
    chain = reorder_by_usage(chain, store)
    return ResilientChatModel(chain, label=f"{backend}:{role}", store=store)
