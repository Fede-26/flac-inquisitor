"""SQLite storage: analysis results, quarantine log, ignores, activity, settings.

One short-lived connection per call keeps this safe to use from the web
threads and the scanner thread at once; WAL lets readers proceed while the
scanner writes.
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

# DATA_PATH locates the database itself, so it can only come from the environment.
DATA_PATH = Path(os.environ.get("DATA_PATH", "/data"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,          -- relative to the library
    album_dir TEXT NOT NULL,            -- relative; disc folders folded into their album
    album_key TEXT NOT NULL,            -- short stable hash of album_dir, used in URLs
    size INTEGER, mtime REAL,
    streaminfo_md5 TEXT,                -- NULL when the encoder left it empty
    audio_md5 TEXT,                     -- MD5 of the decoded audio
    sample_rate INTEGER, bits INTEGER, channels INTEGER, duration REAL,
    artist TEXT, album TEXT, title TEXT, disc INTEGER, track INTEGER,
    algo_version INTEGER,
    verdict TEXT, categories TEXT, summary TEXT, evidence TEXT, metrics TEXT,
    album_evidence TEXT,                -- JSON evidence from album coherence, or NULL
    final_verdict TEXT,                 -- verdict including album coherence
    final_categories TEXT,
    error TEXT,
    analyzed_at TEXT,
    missing INTEGER NOT NULL DEFAULT 0,
    cd_status TEXT,                     -- verified | mismatch | not-found | unsupported, NULL if not checked
    cd_ar INTEGER,                      -- AccurateRip confidence of the match
    cd_ctdb INTEGER,                    -- CTDB confidence of the match
    cd_offset INTEGER                   -- shift in samples that made the match, from an offset search
);
CREATE INDEX IF NOT EXISTS tracks_album ON tracks(album_dir);
CREATE INDEX IF NOT EXISTS tracks_album_key ON tracks(album_key);
CREATE INDEX IF NOT EXISTS tracks_md5 ON tracks(audio_md5);

CREATE TABLE IF NOT EXISTS ignores (
    audio_md5 TEXT PRIMARY KEY,         -- survives retagging and renames
    note TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quarantine (
    id INTEGER PRIMARY KEY,
    batch TEXT NOT NULL,                -- one quarantine action
    original_path TEXT NOT NULL,        -- relative to the library
    stored_path TEXT NOT NULL,          -- relative to the quarantine folder
    is_audio INTEGER NOT NULL,          -- 0 for sidecars (.lrc, covers)
    album_dir TEXT, artist TEXT, album TEXT, title TEXT, disc INTEGER, track INTEGER,
    audio_md5 TEXT, verdict TEXT, categories TEXT,
    status TEXT NOT NULL DEFAULT 'quarantined',  -- quarantined | restored | deleted
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS quarantine_status ON quarantine(status);

CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cd_lookups (
    url TEXT PRIMARY KEY,
    body BLOB,                          -- NULL when the database has no entry
    fetched_at REAL NOT NULL
);
"""

# Columns added after the first release, for databases created before them.
ADDED_COLUMNS = {"tracks": ["cd_status TEXT", "cd_ar INTEGER", "cd_ctdb INTEGER", "cd_offset INTEGER"]}

JSON_COLUMNS = ("categories", "evidence", "metrics", "album_evidence", "final_categories")


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def db_path() -> Path:
    return DATA_PATH / "flac-inquisitor.db"


@contextmanager
def connect():
    conn = sqlite3.connect(db_path(), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init() -> None:
    DATA_PATH.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        for table, columns in ADDED_COLUMNS.items():
            existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            for column in columns:
                if column.split()[0] not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column}")


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for col in JSON_COLUMNS:
        if col in d and d[col] is not None:
            d[col] = json.loads(d[col])
    return d


def log(kind: str, message: str) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO activity (at, kind, message) VALUES (?, ?, ?)", (now(), kind, message))


def recent_activity(limit: int = 10) -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM activity ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_setting(key: str) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
