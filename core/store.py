import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_PREFS: Dict[str, Any] = {
    "language": "es",
    "response_style": "conciso",
    "music_channel": "discord",
}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    """Memoria estructurada local: SQLite (datos/eventos) + JSON (preferencias)."""

    def __init__(self, base_dir) -> None:
        self.base = Path(base_dir)
        self.base.mkdir(parents=True, exist_ok=True)
        self.db_path = self.base / "claudio.db"
        self.prefs_path = self.base / "preferences.json"
        self._init_db()
        if not self.prefs_path.exists():
            self._write_prefs(dict(DEFAULT_PREFS))

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS facts ("
                "key TEXT PRIMARY KEY, value TEXT NOT NULL, "
                "source TEXT DEFAULT '', updated TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS events ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, "
                "content TEXT NOT NULL, ts TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_kind_ts ON events(kind, ts)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS backend_health ("
                "name TEXT PRIMARY KEY, status TEXT NOT NULL, "
                "until_ts REAL DEFAULT 0, fails INTEGER DEFAULT 0, updated TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS llm_usage ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, backend TEXT NOT NULL, "
                "role TEXT DEFAULT '', in_chars INTEGER DEFAULT 0, out_chars INTEGER DEFAULT 0, "
                "ok INTEGER DEFAULT 1, err TEXT DEFAULT '')"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_llm_usage_ts ON llm_usage(ts)"
            )

    def remember(self, key: str, value: str, source: str = "") -> bool:
        key = (key or "").strip().lower()
        value = (value or "").strip()
        if not key or not value:
            return False
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO facts(key, value, source, updated) VALUES(?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "source=excluded.source, updated=excluded.updated",
                (key, value, source, _now()),
            )
        return True

    def recall(self, key: str) -> Optional[str]:
        key = (key or "").strip().lower()
        if not key:
            return None
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM facts WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def forget(self, key: str) -> bool:
        key = (key or "").strip().lower()
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM facts WHERE key=?", (key,))
            return cur.rowcount > 0

    def list_facts(self, prefix: str = "", limit: int = 100) -> Dict[str, str]:
        prefix = (prefix or "").strip().lower()
        with self._connect() as conn:
            if prefix:
                rows = conn.execute(
                    "SELECT key, value FROM facts WHERE key LIKE ? ORDER BY key LIMIT ?",
                    (prefix + "%", max(1, min(limit, 500))),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT key, value FROM facts ORDER BY key LIMIT ?",
                    (max(1, min(limit, 500)),),
                ).fetchall()
        return {k: v for k, v in rows}

    def log_event(self, kind: str, content: str) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO events(kind, content, ts) VALUES(?,?,?)",
                ((kind or "info").strip() or "info", (content or "").strip(), _now()),
            )
            return cur.lastrowid

    def recent_events(self, kind: str = "", limit: int = 20) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if (kind or "").strip():
                rows = conn.execute(
                    "SELECT id, kind, content, ts FROM events WHERE kind=? "
                    "ORDER BY id DESC LIMIT ?",
                    (kind.strip(), max(1, min(limit, 200))),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, kind, content, ts FROM events ORDER BY id DESC LIMIT ?",
                    (max(1, min(limit, 200)),),
                ).fetchall()
        return [{"id": r[0], "kind": r[1], "content": r[2], "ts": r[3]} for r in rows]

    def get_backend_state(self, name: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status, until_ts, fails FROM backend_health WHERE name=?", (name,),
            ).fetchone()
        if not row:
            return None
        return {"name": name, "status": row[0], "until": row[1], "fails": row[2]}

    def set_backend_state(self, name: str, status: str, until_ts: float = 0.0) -> None:
        with self._connect() as conn:
            if status == "ok":
                conn.execute(
                    "INSERT INTO backend_health(name, status, until_ts, fails, updated) "
                    "VALUES(?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET status='ok', "
                    "until_ts=0, fails=0, updated=excluded.updated",
                    (name, "ok", 0.0, 0, _now()),
                )
            else:
                conn.execute(
                    "INSERT INTO backend_health(name, status, until_ts, fails, updated) "
                    "VALUES(?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET status=excluded.status, "
                    "until_ts=excluded.until_ts, fails=backend_health.fails+1, updated=excluded.updated",
                    (name, status, float(until_ts or 0.0), 0, _now()),
                )

    def _read_prefs(self) -> Dict[str, Any]:
        try:
            data = json.loads(self.prefs_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write_prefs(self, prefs: Dict[str, Any]) -> None:
        tmp = self.prefs_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(prefs, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.prefs_path)

    def log_llm_call(self, backend: str, in_chars: int = 0, out_chars: int = 0,
                     ok: bool = True, err: str = "") -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO llm_usage(ts, backend, role, in_chars, out_chars, ok, err) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (_now(), backend, "", int(in_chars or 0), int(out_chars or 0),
                     1 if ok else 0, (err or "")[:200]),
                )
        except Exception:
            pass

    def llm_usage_today(self) -> Dict[str, Dict[str, int]]:
        from datetime import date
        day = date.today().isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT backend, COUNT(*), COALESCE(SUM(in_chars),0), "
                "COALESCE(SUM(out_chars),0), SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END) "
                "FROM llm_usage WHERE ts >= ? GROUP BY backend",
                (day,),
            ).fetchall()
        return {r[0]: {"calls": r[1], "tokens": (r[2] + r[3]) // 4, "errors": r[4]} for r in rows}

    def all_prefs(self) -> Dict[str, Any]:
        merged = dict(DEFAULT_PREFS)
        merged.update(self._read_prefs())
        return merged

    def get_pref(self, key: str, default: Any = None) -> Any:
        return self.all_prefs().get((key or "").strip(), default)

    def set_pref(self, key: str, value: Any) -> Dict[str, Any]:
        prefs = self._read_prefs()
        prefs[(key or "").strip()] = value
        self._write_prefs(prefs)
        return self.all_prefs()
