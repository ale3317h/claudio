import os
import shutil
from datetime import datetime
from typing import Any, Dict, List

from plugins.base import IntentPattern, PluginBase, PluginManifest, ToolSchema

_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
          "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def _ahora() -> datetime:
    return datetime.now()


def fecha_larga(now: datetime) -> str:
    return f"{_DIAS[now.weekday()]} {now.day} de {_MESES[now.month - 1]} de {now.year}"


class Plugin(PluginBase):
    manifest = PluginManifest(
        name="system",
        version="1.0.0",
        description="Hora, fecha y estado del sistema al instante",
        author="CLAUDIO",
        category="system",
        dependencies=[],
        permissions=[],
    )

    async def initialize(self, core) -> None:
        self.core = core

    async def shutdown(self) -> None:
        pass

    async def tool_system_time(self) -> Dict[str, Any]:
        now = _ahora()
        return {
            "ok": True,
            "fecha": fecha_larga(now),
            "hora": now.strftime("%H:%M"),
            "iso": now.isoformat(timespec="seconds"),
        }

    async def tool_system_stats(self) -> Dict[str, Any]:
        import psutil
        drive = os.path.join(os.environ.get("SYSTEMDRIVE", "C:"), "\\")
        disk = shutil.disk_usage(drive)
        vm = psutil.virtual_memory()
        return {
            "ok": True,
            "cpu_pct": psutil.cpu_percent(interval=0.5),
            "ram_pct": vm.percent,
            "ram_total_gb": round(vm.total / (1024 ** 3), 1),
            "disco_pct": round(disk.used / disk.total * 100, 1),
            "disco_libre_gb": round(disk.free / (1024 ** 3), 1),
        }

    def get_tools(self) -> List[ToolSchema]:
        return [
            ToolSchema(
                name="system_time",
                description="Fecha y hora actual. SIEMPRE usar para preguntas de hora/dia/fecha.",
                parameters={},
            ),
            ToolSchema(
                name="system_stats",
                description="Uso de CPU, RAM y disco en este momento.",
                parameters={},
            ),
        ]

    def get_intents(self) -> List[IntentPattern]:
        return [
            IntentPattern(
                name="system_time",
                patterns=[
                    r"qu[eé] hora es",
                    r"la hora",
                    r"qu[eé] d[ií]a es",
                    r"qu[eé] fecha",
                    r"hoy es",
                    r"que dia",
                ],
                entities=[],
            ),
            IntentPattern(
                name="system_stats",
                patterns=[
                    r"\bcpu\b",
                    r"\bram\b",
                    r"disco",
                    r"memoria del sistema",
                    r"como est[aá] la (pc|compu|m[aá]quina|sistema)",
                    r"rendimiento",
                    r"system specs",
                ],
                entities=[],
            ),
        ]
