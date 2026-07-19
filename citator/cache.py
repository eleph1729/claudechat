"""Tiny sqlite cache so each case is only analysed once."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

DEFAULT_TTL = 7 * 24 * 3600  # a week


def _default_path() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "fcl-citator" / "cache.db"


class Cache:
    def __init__(self, path: str | Path | None = None, ttl: int = DEFAULT_TTL):
        self.path = Path(path) if path else _default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl
        with self._conn() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS results "
                "(key TEXT PRIMARY KEY, created REAL NOT NULL, payload TEXT NOT NULL)"
            )

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def get(self, key: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT created, payload FROM results WHERE key = ?", (key,)
            ).fetchone()
        if row is None or time.time() - row[0] > self.ttl:
            return None
        return json.loads(row[1])

    def set(self, key: str, payload: dict) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO results (key, created, payload) VALUES (?, ?, ?)",
                (key, time.time(), json.dumps(payload)),
            )
