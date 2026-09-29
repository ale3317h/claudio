"""Kling Studio (simple): genera videos con Kling 3.0 Turbo image-to-video.

Uso:
    set KLING_API_KEY=tu_key   (o pegala en el campo)
    .venv\\Scripts\\python scripts\\kling_studio.py

Solo stdlib (tkinter + urllib). La key NO se guarda en ningun archivo.
"""
import base64
import json
import mimetypes
import os
import threading
import tkinter as tk
import urllib.request
from tkinter import filedialog, messagebox, ttk

API_BASE = "https://api-singapore.klingai.com"
CREATE_URL = API_BASE + "/image-to-video/kling-3.0-turbo"


def _req(method: str, url: str, api_key: str, body=None, timeout: int = 60,
         retries_429: int = 4):
    """POST/GET con reintento ante 429 (límite de velocidad de Kling)."""
    import time
    import urllib.error
    data = json.dumps(body).encode() if body is not None else None
    wait = 20
    for attempt in range(retries_429 + 1):
        try:
            r = urllib.request.Request(url, data=data, method=method, headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            })
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries_429:
                try:
                    wait = int(e.headers.get("Retry-After", wait))
                except (TypeError, ValueError):
                    pass
                time.sleep(wait)
                wait = min(wait * 2, 120)
                continue
            raise


def create_task(api_key: str, prompt: str, image_b64_uri: str,
                resolution: str, duration: int, watermark: bool) -> str:
    payload = {
        "contents": [
            {"type": "prompt", "text": prompt},
            {"type": "first_frame", "url": image_b64_uri},
        ],
        "settings": {"resolution": resolution, "duration": int(duration)},
        "options": {"watermark_info": {"enabled": bool(watermark)}},
    }
    res = _req("POST", CREATE_URL, api_key, payload, timeout=60)
    if res.get("code") != 0:
        raise RuntimeError(f"API {res.get('code')}: {res.get('message')}")
    return res["data"]["id"]


def poll_task(api_key: str, task_id: str, every_s: int = 5,
              max_tries: int = 144, log=print) -> dict:
    import time
    for _ in range(max_tries):
        res = _req("GET", f"{API_BASE}/tasks?task_ids={task_id}", api_key)
        if res.get("code") != 0:
            raise RuntimeError(f"API {res.get('code')}: {res.get('message')}")
        items = res.get("data") or []
        if not items:
            raise RuntimeError("Sin datos de tarea")
        st = items[0].get("status", "")
        log(f"estado: {st}")
        if st == "succeeded":
            return items[0]
        if st == "failed":
            raise RuntimeError(items[0].get("message") or "Tarea fallida")
        time.sleep(every_s)
    raise TimeoutError("Timeout esperando el video")


def image_to_data_uri(path: str) -> str:
    size = os.path.getsize(path)
    if size > 50 * 1024 * 1024:
        raise ValueError("La imagen supera 50MB")
    mime, _ = mimetypes.guess_type(path)
    if not mime or not mime.startswith("image/"):
        raise ValueError("Formato no soportado (usa .jpg/.jpeg/.png)")
    with open(path, "rb") as f:
        raw = f.read()
    return f"data:{mime};base64," + base64.b64encode(raw).decode()


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Kling Studio (simple)")
        root.minsize(540, 520)
        self.video_url = ""
        frm = ttk.Frame(root, padding=10)
        frm.grid(sticky="nsew")
        root.columnconfigure(0, weight=1)

        ttk.Label(frm, text="API Key:").grid(row=0, column=0, sticky="w")
        self.key = ttk.Entry(frm, width=60, show="*")
        self.key.grid(row=0, column=1, sticky="ew", pady=2)
        self.key.insert(0, os.getenv("KLING_API_KEY", ""))

        ttk.Label(frm, text="Prompt:").grid(row=1, column=0, sticky="nw")
        self.prompt = tk.Text(frm, width=60, height=5)
        self.prompt.grid(row=1, column=1, sticky="ew", pady=2)

        ttk.Label(frm, text="Imagen de referencia:").grid(
            row=2, column=0, sticky="w")
        box = ttk.Frame(frm)
        box.grid(row=2, column=1, sticky="ew", pady=2)
        box.columnconfigure(0, weight=1)
        ttk.Button(box, text="📁 ELEGIR IMAGEN...",
                   command=self.pick).grid(row=0, column=0, sticky="ew")
        self.img_path = tk.StringVar(value="(ninguna elegida)")
        ttk.Label(box, textvariable=self.img_path, wraplength=420,
                  foreground="gray").grid(row=1, column=0, sticky="w")

        ttk.Label(frm, text="Resolución:").grid(row=3, column=0, sticky="w")
        self.res = ttk.Combobox(frm, values=["720p", "1080p"],
                                state="readonly", width=10)
        self.res.set("720p")
        self.res.grid(row=3, column=1, sticky="w", pady=2)

        ttk.Label(frm, text="Duración (3-15s):").grid(row=4, column=0, sticky="w")
        self.dur = ttk.Spinbox(frm, from_=3, to=15, width=5)
        self.dur.set("5")
        self.dur.grid(row=4, column=1, sticky="w", pady=2)

        self.wm = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm, text="Con marca de agua",
                        variable=self.wm).grid(row=5, column=1, sticky="w")

        self.go = ttk.Button(frm, text="Generar video", command=self.start)
        self.go.grid(row=6, column=1, sticky="ew", pady=6)

        self.status = ttk.Label(frm, text="Listo. Imagen: .jpg/.png, "
                                "min 300px, aspecto entre 1:2.5 y 2.5:1.",
                                wraplength=480)
        self.status.grid(row=7, column=0, columnspan=2, sticky="w")

        row = ttk.Frame(frm)
        row.grid(row=8, column=0, columnspan=2, sticky="ew", pady=2)
        self.open_btn = ttk.Button(row, text="Abrir video",
                                   command=self.open_url, state="disabled")
        self.open_btn.pack(side="left")
        self.dl_btn = ttk.Button(row, text="Descargar",
                                 command=self.download, state="disabled")
        self.dl_btn.pack(side="left", padx=4)

    def pick(self):
        p = filedialog.askopenfilename(
            filetypes=[("Imágenes", "*.jpg *.jpeg *.png")])
        if p:
            self.img_path.set(p)

    def say(self, msg: str):
        self.root.after(0, lambda: self.status.config(text=msg))

    def start(self):
        api_key = self.key.get().strip()
        prompt = self.prompt.get("1.0", "end").strip()
        img = self.img_path.get().strip()
        if not api_key:
            messagebox.showerror("Falta", "Pegá tu API Key")
            return
        if not prompt:
            messagebox.showerror("Falta", "Escribí un prompt")
            return
        if not img or img == "(ninguna elegida)":
            messagebox.showerror("Falta", "Elegí una imagen")
            return
        if getattr(self, "busy", False):
            messagebox.showinfo("En curso", "Ya hay una generación en marcha")
            return
        self.busy = True
        self.go.config(state="disabled")
        self.open_btn.config(state="disabled")
        self.dl_btn.config(state="disabled")
        threading.Thread(target=self.run, args=(api_key, prompt, img),
                         daemon=True).start()

    def run(self, api_key, prompt, img):
        try:
            self.say("Leyendo imagen...")
            uri = image_to_data_uri(img)
            self.say("Creando tarea...")
            tid = create_task(api_key, prompt, uri, self.res.get(),
                              int(self.dur.get()), self.wm.get())
            self.say(f"Tarea {tid}. Generando (puede tardar minutos)...")
            item = poll_task(api_key, tid, every_s=8, log=self.say)
            outs = [o for o in (item.get("outputs") or [])
                    if o.get("type") == "video" and o.get("url")]
            if not outs:
                raise RuntimeError("Sin URL de video en la respuesta")
            self.video_url = outs[0]["url"]
            self.say(f"Listo ({outs[0].get('duration', '?')}s). "
                     "El link vence en 30 días: descargalo.")
            self.root.after(0, lambda: (
                self.open_btn.config(state="normal"),
                self.dl_btn.config(state="normal"),
                self.go.config(state="normal")))
        except Exception as e:
            self.say(f"Error: {e}")
            self.root.after(0, lambda: self.go.config(state="normal"))
        finally:
            self.busy = False

    def open_url(self):
        import webbrowser
        if self.video_url:
            webbrowser.open(self.video_url)

    def download(self):
        if not self.video_url:
            return
        dest = filedialog.asksaveasfilename(defaultextension=".mp4",
                                            filetypes=[("Video", "*.mp4")])
        if not dest:
            return
        self.say("Descargando...")
        threading.Thread(target=self._dl, args=(dest,), daemon=True).start()

    def _dl(self, dest):
        try:
            urllib.request.urlretrieve(self.video_url, dest)
            self.say(f"Guardado en {dest}")
        except Exception as e:
            self.say(f"Error descargando: {e}")


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
