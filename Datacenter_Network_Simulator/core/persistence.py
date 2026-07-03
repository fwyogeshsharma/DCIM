"""
SQLite-backed session persistence.

The simulator holds all runtime state in memory (AppState). When the host
process restarts — on the GCP VM this happens at the daily 04:30 boot — that
state is lost: topology, IP bindings, running simulators, rule/tick config,
energy counters. This module gives AppState a durable, single-file store so it
can snapshot state on change and restore it on startup.

Why SQLite, not JSON files or Postgres:
  * Single-node, single-writer app — no need for a client/server RDBMS, and a
    local Postgres would just have to survive the same reboot.
  * Real ACID transactions — an atomic COMMIT replaces the manual
    write-tmp-then-rename dance, safe even if the process is killed mid-write.
  * One file on the persistent boot disk — zero extra service to keep alive.
  * Room to grow: later phases store per-device energy counters, traps and
    console logs as real tables with history/query, not just latest-value blobs.

Design notes:
  * `kv` holds JSON document blobs (topology, floorplan, config manifest) keyed
    by name. Rare writes, whole-document replace.
  * One connection shared across threads (API worker + metric-tick flusher),
    serialized by a lock. Volume is tiny so contention is a non-issue.
  * WAL journal + synchronous=NORMAL: durable across process crash, and a
    reader never blocks the writer.
  * Every public method swallows and logs errors — persistence must NEVER take
    down the app or block startup. A missing/corrupt store degrades to "no
    saved state", not a crash.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("persistence")

# Bump when the on-disk schema changes in a non-additive way.
SCHEMA_VERSION = 1


def _default_db_path() -> str:
    """`<project_root>/data/state/session.db` — project root is the parent of
    the directory holding this file (core/)."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "data", "state", "session.db")


class SessionStore:
    def __init__(self, db_path: Optional[str] = None):
        self._path = db_path or _default_db_path()
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._open()

    # ── lifecycle ────────────────────────────────────────────────────────────
    def _open(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            # check_same_thread=False: the connection is shared between the API
            # worker threads and the tick flusher; all access is serialized by
            # self._lock, so cross-thread use is safe.
            self._conn = sqlite3.connect(
                self._path, check_same_thread=False, timeout=5.0
            )
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._init_schema()
            log.info("session store ready at %s", self._path)
        except Exception:
            # A store we cannot open must not stop the app — run stateless.
            log.exception("failed to open session store at %s — running without persistence", self._path)
            self._conn = None

    def _init_schema(self) -> None:
        assert self._conn is not None
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS kv (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        # Traps are discrete, queryable events — a real table, not a blob.
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS traps (
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                ts   TEXT,
                data TEXT NOT NULL
            )
            """
        )
        self._conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

    @property
    def available(self) -> bool:
        return self._conn is not None

    # ── blob documents (kv) ──────────────────────────────────────────────────
    def set_blob(self, key: str, obj: Any) -> None:
        """Upsert a JSON-serializable document under `key`. No-op on failure."""
        if self._conn is None:
            return
        try:
            payload = json.dumps(obj, separators=(",", ":"))
        except (TypeError, ValueError):
            log.exception("blob %r is not JSON-serializable — not persisted", key)
            return
        ts = datetime.now(timezone.utc).isoformat()
        with self._lock:
            if self._conn is None:
                return
            try:
                self._conn.execute(
                    "INSERT INTO kv(key, value, updated_at) VALUES(?,?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (key, payload, ts),
                )
                self._conn.commit()
            except Exception:
                log.exception("failed to persist blob %r", key)

    def get_blob(self, key: str) -> Optional[Any]:
        """Return the stored document for `key`, or None if missing/corrupt."""
        if self._conn is None:
            return None
        with self._lock:
            if self._conn is None:
                return None
            try:
                row = self._conn.execute(
                    "SELECT value FROM kv WHERE key=?", (key,)
                ).fetchone()
            except Exception:
                log.exception("failed to read blob %r", key)
                return None
        if row is None:
            return None
        try:
            return json.loads(row[0])
        except (TypeError, ValueError):
            log.exception("blob %r is corrupt — ignoring", key)
            return None

    def delete_blob(self, key: str) -> None:
        if self._conn is None:
            return
        with self._lock:
            if self._conn is None:
                return
            try:
                self._conn.execute("DELETE FROM kv WHERE key=?", (key,))
                self._conn.commit()
            except Exception:
                log.exception("failed to delete blob %r", key)

    # ── traps table ──────────────────────────────────────────────────────────
    def append_trap(self, record: dict, keep: int = 1000) -> None:
        """Append one trap record and trim the table to the last `keep` rows."""
        if self._conn is None:
            return
        try:
            payload = json.dumps(record, separators=(",", ":"))
        except (TypeError, ValueError):
            log.exception("trap record not JSON-serializable — not persisted")
            return
        ts = str(record.get("timestamp", ""))
        with self._lock:
            if self._conn is None:
                return
            try:
                self._conn.execute("INSERT INTO traps(ts, data) VALUES(?,?)", (ts, payload))
                self._conn.execute(
                    "DELETE FROM traps WHERE id <= (SELECT MAX(id) FROM traps) - ?", (keep,)
                )
                self._conn.commit()
            except Exception:
                log.exception("failed to persist trap")

    def recent_traps(self, limit: int = 1000) -> list:
        """Return up to `limit` most-recent trap records, oldest-first (matching
        the in-memory trap_history ordering: newest at the end)."""
        if self._conn is None:
            return []
        with self._lock:
            if self._conn is None:
                return []
            try:
                rows = self._conn.execute(
                    "SELECT data FROM traps ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
            except Exception:
                log.exception("failed to read traps")
                return []
        out = []
        for (data,) in reversed(rows):  # DESC → reverse to oldest-first
            try:
                out.append(json.loads(data))
            except (TypeError, ValueError):
                continue
        return out

    def clear_traps(self) -> None:
        if self._conn is None:
            return
        with self._lock:
            if self._conn is None:
                return
            try:
                self._conn.execute("DELETE FROM traps")
                self._conn.commit()
            except Exception:
                log.exception("failed to clear traps")
