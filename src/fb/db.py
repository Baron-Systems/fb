from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS kv (
  k TEXT PRIMARY KEY,
  v TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
  agent_id TEXT PRIMARY KEY,
  agent_name TEXT,
  created_at INTEGER NOT NULL,
  last_seen INTEGER NOT NULL,
  base_url TEXT NOT NULL,
  shared_secret TEXT NOT NULL,
  meta_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  target TEXT NOT NULL,
  ok INTEGER NOT NULL,
  detail_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS backups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  agent_id TEXT NOT NULL,
  stack TEXT NOT NULL,
  site TEXT NOT NULL,
  backup_dir TEXT NOT NULL,
  manifest_json TEXT NOT NULL,
  rating INTEGER,
  feedback TEXT
);

CREATE TABLE IF NOT EXISTS telegram_settings (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  bot_token TEXT,
  chat_id TEXT,
  enabled INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS notifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  type TEXT NOT NULL,
  title TEXT NOT NULL,
  message TEXT NOT NULL,
  is_read INTEGER DEFAULT 0
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False allows SQLite to be used across threads
    # This is safe with WAL mode which we enable in SCHEMA_SQL
    cx = sqlite3.connect(db_path, check_same_thread=False)
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA foreign_keys=ON;")
    cx.executescript(SCHEMA_SQL)
    return cx


def kv_get(cx: sqlite3.Connection, k: str, default: Any | None = None) -> Any:
    row = cx.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row["v"])
    except Exception:
        return default


def kv_set(cx: sqlite3.Connection, k: str, v: Any) -> None:
    cx.execute(
        "INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
        (k, json.dumps(v)),
    )
    cx.commit()


def now_ts() -> int:
    return int(time.time())


