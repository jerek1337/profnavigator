"""Хранилище на SQLite: пользователи, события для аналитики, служебные значения.

SQLite встроен в Python и не требует сервера. Для большой нагрузки класс можно
заменить реализацией на PostgreSQL с тем же набором методов.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from .models import User

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    uid TEXT PRIMARY KEY,
    chat_id TEXT,
    name TEXT,
    state TEXT NOT NULL DEFAULT 'new',
    answers TEXT NOT NULL DEFAULT '{}',
    result TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    completed_at REAL,
    reminded_at REAL,
    reminder_count INTEGER NOT NULL DEFAULT 0,
    reminders_off INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_users_completed ON users(completed_at);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL,
    channel TEXT NOT NULL,
    name TEXT NOT NULL,
    data TEXT,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_uid_name_ts ON events(uid, name, ts);
CREATE INDEX IF NOT EXISTS idx_events_name ON events(name);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_USER_COLUMNS = ("uid", "chat_id", "name", "state", "answers", "result", "created_at", "updated_at",
                 "completed_at", "reminded_at", "reminder_count", "reminders_off")

def _loads(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except ValueError:
        return default

class Storage:
    def __init__(self, path: str, clock: Callable[[], float] = time.time) -> None:
        self.clock = clock
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=10)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            if path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)

    def _fetch(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    def _execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).rowcount

    def ping(self) -> bool:
        return bool(self._fetch("SELECT 1"))

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _to_user(row: sqlite3.Row) -> User:
        return User(
            uid=row["uid"], chat_id=row["chat_id"], name=row["name"], state=row["state"],
            answers=_loads(row["answers"], {}), result=_loads(row["result"], None),
            created_at=row["created_at"], updated_at=row["updated_at"],
            completed_at=row["completed_at"], reminded_at=row["reminded_at"],
            reminder_count=row["reminder_count"], reminders_off=bool(row["reminders_off"]),
        )

    def get_user(self, uid: str) -> User | None:
        rows = self._fetch("SELECT * FROM users WHERE uid = ?", (uid,))
        return self._to_user(rows[0]) if rows else None

    def get_or_create_user(self, uid: str, *, chat_id: str | None = None, name: str | None = None) -> User:
        user = self.get_user(uid)
        if user is None:
            now = self.clock()
            user = User(uid=uid, chat_id=chat_id, name=name, created_at=now, updated_at=now)
            self.save_user(user)
            return user
        changed = False
        if chat_id and chat_id != user.chat_id:
            user.chat_id, changed = chat_id, True
        if name and name != user.name:
            user.name, changed = name, True
        if changed:
            self.save_user(user)
        return user

    def save_user(self, user: User) -> None:
        user.updated_at = self.clock()
        values = (
            user.uid, user.chat_id, user.name, user.state,
            json.dumps(user.answers, ensure_ascii=False),
            json.dumps(user.result, ensure_ascii=False) if user.result is not None else None,
            user.created_at or user.updated_at, user.updated_at, user.completed_at, user.reminded_at,
            user.reminder_count, int(user.reminders_off),
        )
        placeholders = ", ".join("?" for _ in _USER_COLUMNS)
        self._execute(f"INSERT OR REPLACE INTO users ({', '.join(_USER_COLUMNS)}) VALUES ({placeholders})", values)

    def delete_user(self, uid: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM users WHERE uid = ?", (uid,))
            self._conn.execute("DELETE FROM events WHERE uid = ?", (uid,))

    def all_users(self) -> list[User]:
        return [self._to_user(r) for r in self._fetch("SELECT * FROM users")]

    def due_reminders(self, threshold_ts: float, max_count: int) -> list[User]:
        rows = self._fetch(
            """SELECT * FROM users
               WHERE result IS NOT NULL AND completed_at IS NOT NULL AND reminders_off = 0
                 AND reminder_count < ? AND COALESCE(reminded_at, completed_at) <= ?
               ORDER BY completed_at LIMIT 200""",
            (max_count, threshold_ts),
        )
        return [self._to_user(r) for r in rows]

    def log_event(self, uid: str, channel: str, name: str, data: dict[str, Any] | None = None) -> None:
        self._execute(
            "INSERT INTO events (uid, channel, name, data, ts) VALUES (?, ?, ?, ?, ?)",
            (uid, channel, name, json.dumps(data, ensure_ascii=False) if data else None, self.clock()),
        )

    def count_events(self, uid: str, name: str, since: float) -> int:
        rows = self._fetch("SELECT COUNT(*) AS n FROM events WHERE uid = ? AND name = ? AND ts >= ?", (uid, name, since))
        return int(rows[0]["n"])

    def events(self, since: float = 0.0) -> list[dict[str, Any]]:
        rows = self._fetch("SELECT uid, channel, name, data, ts FROM events WHERE ts >= ? ORDER BY id", (since,))
        return [{"uid": r["uid"], "channel": r["channel"], "name": r["name"],
                 "data": _loads(r["data"], {}), "ts": r["ts"]} for r in rows]

    def kv_get(self, key: str) -> str | None:
        rows = self._fetch("SELECT value FROM kv WHERE key = ?", (key,))
        return rows[0]["value"] if rows else None

    def kv_set(self, key: str, value: str) -> None:
        self._execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, value))
