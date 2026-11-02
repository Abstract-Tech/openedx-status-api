import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

from .models import Result

RAW_KEEP_DAYS = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS results (check_id TEXT, ts INTEGER, ok INTEGER, latency_ms INTEGER, detail TEXT);
CREATE INDEX IF NOT EXISTS results_idx ON results(check_id, ts);
CREATE TABLE IF NOT EXISTS rollups (check_id TEXT, day TEXT, ok INTEGER, total INTEGER, PRIMARY KEY(check_id, day));
CREATE TABLE IF NOT EXISTS incidents (id INTEGER PRIMARY KEY AUTOINCREMENT, check_id TEXT, title TEXT, started INTEGER, resolved INTEGER);
"""


def day_of(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


class Store:
    def __init__(self, path: str):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)

    def close(self):
        with self._lock:
            self._db.close()

    def record(self, check_id: str, r: Result):
        with self._lock, self._db:
            self._db.execute("INSERT INTO results VALUES (?,?,?,?,?)", (check_id, r.ts, int(r.ok), r.latency_ms, r.detail))
            self._db.execute(
                "INSERT INTO rollups VALUES (?,?,?,1) ON CONFLICT(check_id, day) DO UPDATE SET "
                "ok = ok + excluded.ok, total = total + 1",
                (check_id, day_of(r.ts), int(r.ok)),
            )

    def latest(self, check_id: str) -> Result | None:
        with self._lock:
            row = self._db.execute(
                "SELECT ts, ok, detail, latency_ms FROM results WHERE check_id=? ORDER BY ts DESC, rowid DESC LIMIT 1", (check_id,)
            ).fetchone()
        return Result(row["ts"], bool(row["ok"]), row["detail"], row["latency_ms"]) if row else None

    def is_failing(self, check_id: str, n: int) -> bool:
        """True when the last n results all failed."""
        with self._lock:
            rows = self._db.execute(
                "SELECT ok FROM results WHERE check_id=? ORDER BY ts DESC, rowid DESC LIMIT ?", (check_id, n)
            ).fetchall()
        return len(rows) == n and not any(r["ok"] for r in rows)

    def failure_streak(self, check_id: str, limit: int) -> tuple[int, int | None]:
        with self._lock:
            rows = self._db.execute(
                "SELECT ts, ok FROM results WHERE check_id=? ORDER BY ts DESC, rowid DESC LIMIT ?", (check_id, limit)
            ).fetchall()
        streak = []
        for r in rows:
            if r["ok"]:
                break
            streak.append(r["ts"])
        return len(streak), (streak[-1] if streak else None)

    def days(self, check_id: str, retention: int = 90, now: int | None = None) -> list[dict]:
        """Exactly `retention` entries oldest->newest, zero-filled."""
        today = datetime.fromtimestamp(now or time.time(), timezone.utc).date()
        wanted = [(today - timedelta(days=i)).isoformat() for i in range(retention - 1, -1, -1)]
        with self._lock:
            rows = self._db.execute(
                "SELECT day, ok, total FROM rollups WHERE check_id=? AND day>=?", (check_id, wanted[0])
            ).fetchall()
        by = {r["day"]: r for r in rows}
        return [{"day": d, "ok": by[d]["ok"] if d in by else 0, "total": by[d]["total"] if d in by else 0} for d in wanted]

    # incidents
    def open_incident(self, check_id: str, title: str, ts: int) -> int:
        with self._lock, self._db:
            return self._db.execute(
                "INSERT INTO incidents(check_id, title, started, resolved) VALUES (?,?,?,NULL)", (check_id, title, ts)
            ).lastrowid

    def open_incident_for(self, check_id: str) -> int | None:
        with self._lock:
            row = self._db.execute("SELECT id FROM incidents WHERE check_id=? AND resolved IS NULL", (check_id,)).fetchone()
        return row["id"] if row else None

    def resolve_incident(self, incident_id: int, ts: int):
        with self._lock, self._db:
            self._db.execute("UPDATE incidents SET resolved=? WHERE id=?", (ts, incident_id))

    def resolve_orphaned_incidents(self, check_ids: list[str], ts: int) -> int:
        """Resolve open incidents whose check no longer exists; returns how many."""
        marks = ",".join("?" * len(check_ids))
        with self._lock, self._db:
            return self._db.execute(
                f"UPDATE incidents SET resolved=? WHERE resolved IS NULL AND check_id NOT IN ({marks})", (ts, *check_ids)
            ).rowcount

    def incidents(self, since_ts: int, limit: int = 20) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(
                "SELECT id, check_id, title, started, resolved FROM incidents WHERE started>=? OR resolved IS NULL "
                "ORDER BY started DESC, id DESC LIMIT ?", (since_ts, limit),
            ).fetchall()

    def prune(self, retention: int = 90, now: int | None = None):
        now = now or int(time.time())
        cutoff_day = day_of(now - retention * 86400)
        with self._lock, self._db:
            self._db.execute("DELETE FROM results WHERE ts<?", (now - RAW_KEEP_DAYS * 86400,))
            self._db.execute("DELETE FROM rollups WHERE day<?", (cutoff_day,))
            self._db.execute("DELETE FROM incidents WHERE resolved IS NOT NULL AND resolved<?", (now - retention * 86400,))
