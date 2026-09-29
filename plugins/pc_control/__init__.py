import ctypes
import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
import uuid
from ctypes import wintypes
from typing import Any, Dict, List, Optional

from plugins.base import IntentPattern, Permission, PluginBase, PluginManifest, ToolParameter, ToolSchema

WM_CLOSE = 0x10
SW_RESTORE = 9
SW_MINIMIZE = 6
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

PUL = ctypes.POINTER(ctypes.c_ulong)


class KeyBdInput(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", PUL),
    ]


class InputUnion(ctypes.Union):
    _fields_ = [("ki", KeyBdInput)]


class Input(ctypes.Structure):
    # Relleno para igualar sizeof(INPUT) real en 64-bit (40 bytes).
    # Sin esto SendInput falla con WinError 87.
    _fields_ = [
        ("type", ctypes.c_ulong),
        ("ii", InputUnion),
        ("_pad", ctypes.c_ulong * 2),
    ]


assert ctypes.sizeof(Input) == 40, f"INPUT mide {ctypes.sizeof(Input)}, debe medir 40"  # noqa: S101 - invariante FFI


user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.GetCurrentThreadId.argtypes = ()
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

user32.EnumWindows.argtypes = (WNDENUMPROC, wintypes.LPARAM)
user32.EnumWindows.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = (wintypes.HWND,)
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetWindowTextW.restype = ctypes.c_int
user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
user32.ShowWindow.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.PostMessageW.restype = wintypes.BOOL
user32.GetForegroundWindow.argtypes = ()
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, wintypes.LPDWORD)
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.BringWindowToTop.argtypes = (wintypes.HWND,)
user32.BringWindowToTop.restype = wintypes.BOOL
user32.AttachThreadInput.argtypes = (wintypes.DWORD, wintypes.DWORD, wintypes.BOOL)
user32.AttachThreadInput.restype = wintypes.BOOL

VK_MODIFIERS = {"ctrl": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B}
VK_SPECIAL = {
    "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B, "space": 0x20,
    "backspace": 0x08, "delete": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23,
    "pgup": 0x21, "pgdn": 0x22, "up": 0x26, "down": 0x28, "left": 0x25,
    "right": 0x27, "capslock": 0x14,
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
}


def _send_input(inp: Input) -> None:
    if user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp)) != 1:
        raise ctypes.WinError(ctypes.get_last_error())


def _key_event(wvk: int = 0, wscan: int = 0, flags: int = 0) -> None:
    ki = KeyBdInput(wVk=wvk, wScan=wscan, dwFlags=flags, time=0, dwExtraInfo=None)
    _send_input(Input(type=INPUT_KEYBOARD, ii=InputUnion(ki=ki)))


def _type_unicode(text: str) -> int:
    count = 0
    for ch in text:
        code = ord(ch)
        if code > 0xFFFF:
            code -= 0x10000
            units = [0xD800 + (code >> 10), 0xDC00 + (code & 0x3FF)]
        else:
            units = [code]
        for unit in units:
            _key_event(wscan=unit, flags=KEYEVENTF_UNICODE)
            _key_event(wscan=unit, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)
            count += 1
    return count


def _vk_down(vk: int) -> None:
    _key_event(wvk=vk, flags=0)


def _vk_up(vk: int) -> None:
    _key_event(wvk=vk, flags=KEYEVENTF_KEYUP)


def _resolve_vk(name: str) -> int:
    key = name.strip().lower()
    if key in VK_MODIFIERS:
        return VK_MODIFIERS[key]
    if key in VK_SPECIAL:
        return VK_SPECIAL[key]
    if len(key) == 1 and (key.isalnum() or key in ".,;-'/\\[]"):
        return ord(key.upper())
    raise ValueError(f"Tecla desconocida: '{name}'")


def _list_windows() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    @WNDENUMPROC
    def callback(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if buf.value.strip():
                    out.append({"hwnd": hwnd, "title": buf.value})
        return True

    user32.EnumWindows(callback, 0)
    return out


def _find_windows(title_contains: str) -> List[Dict[str, Any]]:
    needle = title_contains.strip().lower()
    return [w for w in _list_windows() if needle in w["title"].lower()]


def _force_foreground(hwnd: int) -> bool:
    user32.ShowWindow(hwnd, SW_RESTORE)
    if user32.SetForegroundWindow(hwnd):
        return True
    user32.ShowWindow(hwnd, SW_MINIMIZE)
    user32.ShowWindow(hwnd, SW_RESTORE)
    if user32.SetForegroundWindow(hwnd):
        return True
    cur_tid = kernel32.GetCurrentThreadId()
    tgt_tid = user32.GetWindowThreadProcessId(hwnd, None)
    user32.AttachThreadInput(cur_tid, tgt_tid, True)
    try:
        user32.BringWindowToTop(hwnd)
        ok = bool(user32.SetForegroundWindow(hwnd))
    finally:
        user32.AttachThreadInput(cur_tid, tgt_tid, False)
    return ok


def _app_aliases() -> Dict[str, Any]:
    local = os.environ.get("LOCALAPPDATA", "")
    appdata = os.environ.get("APPDATA", "")
    pf86 = os.environ.get("PROGRAMFILES(X86)", "")
    return {
        "discord": (os.path.join(local, "Discord", "Update.exe"), "--processStart Discord.exe"),
        "spotify": (os.path.join(appdata, "Spotify", "Spotify.exe"), ""),
        "opera": (_first_existing([
            os.path.join(local, "Programs", "Opera GX", "opera.exe"),
            os.path.join(local, "Programs", "Opera", "opera.exe"),
            os.path.join(pf86, "Opera", "opera.exe"),
        ]), ""),
    }


def _first_existing(paths) -> str:
    for p in paths:
        if p and os.path.exists(p):
            return p
    return ""


def _launch_store_app(name: str) -> Optional[Dict[str, Any]]:
    safe = "".join(c for c in name.strip().lower() if c.isalnum() or c in (" ", "-", "_")).strip()
    if not safe:
        return None
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             f"(Get-StartApps | Where-Object {{ $_.Name -like '*{safe}*' }} | Select-Object -First 1).AppID"],
            capture_output=True, text=True, timeout=30, **_silent_popen_kwargs(),
        )
        appid = (r.stdout or "").strip()
        if appid:
            subprocess.Popen(
                ["explorer.exe", f"shell:appsFolder\\{appid}"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, close_fds=True,
            )
            time.sleep(1.0)
            return {"ok": True, "app": name, "via": "store", "appid": appid}
    except Exception:
        pass
    return None


_JOBS_FILE = None


def _persist_jobs() -> None:
    if _JOBS_FILE is None:
        return
    try:
        _JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _JOBS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_JOBS, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(_JOBS_FILE)
    except Exception:
        pass


def _restore_jobs(path) -> None:
    global _JOBS_FILE
    _JOBS_FILE = path
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            with _JOBS_LOCK:
                _JOBS.clear()
                _JOBS.update(data)
    except Exception:
        pass
    changed = False
    with _JOBS_LOCK:
        for job in _JOBS.values():
            if job.get("status") in ("queued", "running"):
                job["status"] = "interrupted"
                job["error"] = "el proceso anterior termino antes de completarla"
                changed = True
    if changed:
        _persist_jobs()


_JOBS: Dict[str, Dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()


def _silent_popen_kwargs() -> Dict[str, Any]:
    if sys.platform != "win32":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
        "startupinfo": si,
    }


_PROCS: Dict[str, Any] = {}  # job_id -> Popen vivo (no se persiste)


def _run_job(job_id: str, command: str) -> None:
    with _JOBS_LOCK:
        if _JOBS.get(job_id, {}).get("status") == "cancelled":
            return
        _JOBS[job_id]["status"] = "running"
        _JOBS[job_id]["started"] = time.time()
    try:
        proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            **_silent_popen_kwargs(),
        )
        _PROCS[job_id] = proc
        try:
            out_text, err_text = proc.communicate(timeout=3600)
            code = proc.returncode
        finally:
            _PROCS.pop(job_id, None)
        out: Dict[str, Any] = {
            "status": "done",
            "code": code,
            "stdout": (out_text or "")[-4000:],
            "stderr": (err_text or "")[-2000:],
        }
    except Exception as e:
        out = {"status": "error", "error": str(e)}
    out["ended"] = time.time()
    with _JOBS_LOCK:
        if _JOBS.get(job_id, {}).get("status") != "cancelled":
            _JOBS[job_id].update(out)
        else:
            _JOBS[job_id]["ended"] = out["ended"]
    _persist_jobs()


def _cancel_job(job_id: str) -> bool:
    """Marca cancelado y mata el proceso si sigue vivo. True si existía."""
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job or job.get("status") in ("done", "error", "cancelled"):
            return job is not None
        job["status"] = "cancelled"
        job["ended"] = time.time()
    proc = _PROCS.pop(job_id, None)
    if proc is not None:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    _persist_jobs()
    return True


class Plugin(PluginBase):
    manifest = PluginManifest(
        name="pc_control",
        version="1.0.0",
        description="Automatizacion de Windows: apps, ventanas, tipeo, hotkeys, shell",
        author="CLAUDIO",
        category="system",
        dependencies=[],
        permissions=[Permission.APP_LAUNCH, Permission.SHELL],
    )

    async def initialize(self, core) -> None:
        if sys.platform != "win32":
            raise RuntimeError("pc_control solo funciona en Windows")
        from core.config import settings
        self.allow_shell = bool(settings.pc_control.allow_shell)
        _restore_jobs(settings.memory.vault_path / "data" / "jobs.json")
        self.core = core

    async def shutdown(self) -> None:
        pass

    def tool_launch_app(self, app_name: str, args: str = "") -> Dict[str, Any]:
        name = app_name.strip().strip('"')
        exe = None
        alias = _app_aliases().get(name.lower())
        if alias is not None:
            alias_exe, alias_args = alias
            if os.path.exists(alias_exe):
                exe = alias_exe
                if not args:
                    args = alias_args
        if (os.path.isabs(name) or os.path.sep in name or "/" in name) and os.path.exists(name):
            exe = name
        if exe is None:
            exe = shutil.which(name) or shutil.which(name + ".exe")
        if exe is None:
            try:
                os.startfile(name)
                return {"ok": True, "app": app_name, "via": "startfile"}
            except Exception:
                pass
        if exe is None:
            store = _launch_store_app(name)
            if store is not None:
                return store
            return {"error": f"No encontre la app '{app_name}'"}
        cmd = [exe] + (shlex.split(args, posix=False) if args else [])
        try:
            flags = getattr(subprocess, "DETACHED_PROCESS", 0)
            proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True,
            )
        except Exception as e:
            return {"error": f"No pude lanzar '{app_name}': {e}"}
        time.sleep(0.5)
        return {"ok": True, "app": app_name, "pid": proc.pid}

    def tool_list_windows(self) -> List[Dict[str, str]]:
        return [{"title": w["title"]} for w in _list_windows()]

    def tool_focus_window(self, title_contains: str) -> Dict[str, Any]:
        matches = _find_windows(title_contains)
        if not matches:
            return {"error": f"No hay ventana con '{title_contains}'"}
        hwnd = matches[0]["hwnd"]
        if not _force_foreground(hwnd):
            return {"error": f"Encontre '{matches[0]['title']}' pero no pude enfocarla"}
        return {"ok": True, "title": matches[0]["title"]}

    def tool_close_window(self, title_contains: str) -> Dict[str, Any]:
        matches = _find_windows(title_contains)
        if not matches:
            return {"error": f"No hay ventana con '{title_contains}'"}
        closed = []
        for w in matches:
            user32.PostMessageW(w["hwnd"], WM_CLOSE, 0, 0)
            closed.append(w["title"])
        return {"ok": True, "closed": closed}

    def tool_type_text(self, text: str) -> Dict[str, Any]:
        try:
            count = _type_unicode(text)
        except Exception as e:
            return {"error": f"No pude tipear: {e}"}
        return {"ok": True, "chars": count}

    def tool_press_hotkey(self, keys: str) -> Dict[str, Any]:
        try:
            parts = [p for p in keys.lower().replace(" ", "").split("+") if p]
            if not parts:
                return {"error": "Especifica teclas, ej: 'ctrl+s'"}
            *mods, main = parts
            vks = [_resolve_vk(m) for m in mods]
            main_vk = _resolve_vk(main)
            for vk in vks:
                _vk_down(vk)
                time.sleep(0.02)
            _vk_down(main_vk)
            time.sleep(0.02)
            _vk_up(main_vk)
            for vk in reversed(vks):
                time.sleep(0.02)
                _vk_up(vk)
        except Exception as e:
            return {"error": f"No pude presionar '{keys}': {e}"}
        return {"ok": True, "keys": keys}

    def tool_run_shell(self, command: str) -> Dict[str, Any]:
        if not self.allow_shell:
            return {"error": "Shell deshabilitado. Activa pc_control.allow_shell: true en config.yaml"}
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                capture_output=True, text=True, timeout=60,
                **_silent_popen_kwargs(),
            )
        except subprocess.TimeoutExpired:
            return {"error": "Timeout (60s)"}
        except Exception as e:
            return {"error": str(e)}
        return {
            "ok": r.returncode == 0,
            "code": r.returncode,
            "stdout": (r.stdout or "")[-4000:],
            "stderr": (r.stderr or "")[-2000:],
        }

    def _shell_allowed(self) -> Any:
        if not self.allow_shell:
            return {"error": "Shell deshabilitado. Activa pc_control.allow_shell: true en config.yaml"}
        return None

    def tool_job_start(self, command: str, name: str = "") -> Dict[str, Any]:
        denied = self._shell_allowed()
        if denied:
            return denied
        job_id = uuid.uuid4().hex[:8]
        with _JOBS_LOCK:
            _JOBS[job_id] = {
                "id": job_id,
                "name": name or command[:40],
                "command": command[:200],
                "status": "queued",
                "created": time.time(),
            }
        threading.Thread(target=_run_job, args=(job_id, command), daemon=True).start()
        _persist_jobs()
        return {"ok": True, "job_id": job_id, "status": "queued"}

    def tool_job_status(self, job_id: str = "") -> Any:
        with _JOBS_LOCK:
            if job_id:
                job = _JOBS.get(job_id)
                if not job:
                    return {"error": f"No existe job '{job_id}'"}
                return {k: v for k, v in job.items() if k not in ("stdout", "stderr")}
            return [{"id": j["id"], "name": j["name"], "status": j["status"]} for j in _JOBS.values()]

    def tool_job_result(self, job_id: str) -> Dict[str, Any]:
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if not job:
                return {"error": f"No existe job '{job_id}'"}
            data = dict(job)
        if data.get("status") not in ("done", "error", "cancelled"):
            return {"ok": False, "status": data.get("status"), "msg": "aun en curso, consulta mas tarde"}
        with _JOBS_LOCK:
            _JOBS.pop(job_id, None)
        return {"ok": True, **data}

    async def tool_job_cancel(self, job_id: str = "") -> Dict[str, Any]:
        """Cancela un job en curso (o todos si se omite job_id). Mata el proceso."""
        targets: list = []
        with _JOBS_LOCK:
            if job_id:
                if job_id in _JOBS:
                    targets = [job_id]
            else:
                targets = [jid for jid, j in _JOBS.items()
                           if j.get("status") in ("queued", "running")]
        if job_id and not targets:
            return {"error": f"No existe job '{job_id}'"}
        for jid in targets:
            _cancel_job(jid)
        _persist_jobs()
        return {"ok": True, "cancelled": targets}

    def get_tools(self) -> List[ToolSchema]:
        return [
            ToolSchema(
                name="launch_app",
                description="Abrir un programa de Windows por nombre o ruta (ej: notepad, calc, spotify, discord). Opcional: args para argumentos.",
                parameters={
                    "app_name": ToolParameter(type="string", description="Nombre o ruta del programa", required=True),
                    "args": ToolParameter(type="string", description="Argumentos opcionales"),
                },
            ),
            ToolSchema(
                name="list_windows",
                description="Listar titulos de ventanas visibles abiertas.",
                parameters={},
            ),
            ToolSchema(
                name="focus_window",
                description="Traer al frente una ventana cuyo titulo contenga el texto dado.",
                parameters={
                    "title_contains": ToolParameter(type="string", description="Parte del titulo de la ventana", required=True),
                },
            ),
            ToolSchema(
                name="close_window",
                description="Cerrar ventana(s) cuyo titulo contenga el texto dado.",
                parameters={
                    "title_contains": ToolParameter(type="string", description="Parte del titulo de la ventana", required=True),
                },
            ),
            ToolSchema(
                name="type_text",
                description="Escribir texto en la ventana enfocada actualmente.",
                parameters={
                    "text": ToolParameter(type="string", description="Texto a escribir", required=True),
                },
            ),
            ToolSchema(
                name="press_hotkey",
                description="Presionar combinacion de teclas en la ventana enfocada, ej: 'ctrl+s', 'alt+f4', 'win+r'.",
                parameters={
                    "keys": ToolParameter(type="string", description="Combinacion separada por +", required=True),
                },
            ),
            ToolSchema(
                name="run_shell",
                description="Ejecutar un comando PowerShell y devolver salida. Requiere pc_control.allow_shell=true.",
                parameters={
                    "command": ToolParameter(type="string", description="Comando a ejecutar", required=True),
                },
            ),
            ToolSchema(
                name="job_start",
                description="Lanzar un comando PowerShell en SEGUNDO PLANO y volver al instante. Devuelve job_id para consultar despues. Requiere pc_control.allow_shell=true.",
                parameters={
                    "command": ToolParameter(type="string", description="Comando a ejecutar en fondo", required=True),
                    "name": ToolParameter(type="string", description="Nombre corto de la tarea"),
                },
            ),
            ToolSchema(
                name="job_status",
                description="Ver estado de tareas en segundo plano (todas o una por job_id).",
                parameters={
                    "job_id": ToolParameter(type="string", description="ID de la tarea (vacio = todas)"),
                },
            ),
            ToolSchema(
                name="job_result",
                description="Obtener salida de una tarea terminada (la elimina de la lista).",
                parameters={
                    "job_id": ToolParameter(type="string", description="ID de la tarea terminada", required=True),
                },
            ),
            ToolSchema(
                name="job_cancel",
                description="Cancelar una tarea en curso (mata el proceso) o todas si se omite el ID. Para 'cancelá/olvidalo'.",
                parameters={
                    "job_id": ToolParameter(type="string", description="ID (vacío = todas las activas)"),
                },
            ),
        ]

    def get_intents(self) -> List[IntentPattern]:
        return [
            IntentPattern(
                name="pc_launch",
                patterns=[r"abr[ií].*", r"lanz[ao].*", r"inici[ao].*.*(programa|app|aplicaci)", r"ejecut[ao].*"],
                entities=["app_name"],
            ),
            IntentPattern(
                name="pc_close",
                patterns=[r"cierr[aoe].*", r"cerr[aoá][a-z]*.*", r"cerr[aoe].*ventana.*"],
                entities=["title_contains"],
            ),
            IntentPattern(
                name="pc_type",
                patterns=[r"escrib[eí].*", r"tipe[aoe].*", r"redact[aoe].*"],
                entities=["text"],
            ),
            IntentPattern(
                name="pc_hotkey",
                patterns=[r"presion[aoe].*tecla.*", r"puls[aoe].*", r"atajo.*", r"combinaci.*tecla.*"],
                entities=["keys"],
            ),
            IntentPattern(
                name="pc_job",
                patterns=[r".*segundo plano.*", r".*fondo.*", r".*job.*", r".*tarea.*"],
                entities=["command"],
            ),
            IntentPattern(
                name="pc_cancel",
                patterns=[r"cancel[aeo].*", r"olvid[ao].*", r"d[eé]jalo.*",
                          r"corta todo.*", r"para todo.*", r"no sigas.*"],
                entities=[],
            ),
        ]
