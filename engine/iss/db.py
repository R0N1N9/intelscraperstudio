"""SQLite storage. One small file in the app folder holds the whole catalog."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS shoots (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT,
    lat REAL,
    lon REAL,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY,
    shoot_id INTEGER REFERENCES shoots(id),
    hash TEXT UNIQUE,
    src_name TEXT,
    src_size INTEGER,
    path TEXT,
    backup_path TEXT,
    kind TEXT,                -- video | photo
    camera TEXT,
    color_profile TEXT,
    duration REAL,
    width INTEGER,
    height INTEGER,
    fps REAL,
    codec TEXT,
    bit_depth INTEGER,
    recorded_at TEXT,
    lat REAL,
    lon REAL,
    meta TEXT,                -- extra metadata as JSON (ISO, lens... when available)
    status TEXT DEFAULT 'imported',   -- imported | analyzing | analyzed | error
    error TEXT,
    score INTEGER,
    verdict TEXT,             -- keep | review | reject (the engine's call)
    user_verdict TEXT,        -- keep | reject (your call; wins over the engine)
    reasons TEXT,             -- JSON list of short sentences
    metrics TEXT,             -- JSON
    best_start REAL,
    best_end REAL,
    thumb TEXT,
    proxy TEXT,
    dhash TEXT,
    similar_to INTEGER
);
CREATE INDEX IF NOT EXISTS clips_shoot ON clips(shoot_id);
CREATE INDEX IF NOT EXISTS clips_src ON clips(src_name, src_size);
CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE,
    folder TEXT,
    title TEXT,
    mtime REAL,
    duration REAL,
    bpm REAL,
    beats TEXT,               -- JSON list of beat times (s)
    energy TEXT,              -- JSON list, one value per second, 0..1
    sections TEXT,            -- JSON list of {name,start,end,energy}
    vibe TEXT,
    vibe_auto TEXT,
    vibe_scores TEXT,         -- JSON
    features TEXT,            -- JSON
    status TEXT DEFAULT 'new',  -- new | analyzed | error
    error TEXT
);
"""

JSON_FIELDS = {"meta", "reasons", "metrics", "beats", "energy", "sections", "vibe_scores", "features", "uses", "user_uses"}

# Columns added after the first release; added in place on older catalogs.
MIGRATIONS = {
    "clips": [
        ("uses", "TEXT"),          # JSON {"stock": bool, "portfolio": bool, "notes": {...}, "print": {...}}
        ("user_uses", "TEXT"),     # JSON overrides, e.g. {"portfolio": true}
        ("companion_of", "INTEGER"),  # JPEG half of a RAW+JPEG pair points at the RAW
        ("megapixels", "REAL"),
    ],
}


def db_path():
    return config.app_dir() / "library.db"


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(db_path(), timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


@contextmanager
def session():
    con = connect()
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init() -> None:
    with session() as con:
        con.executescript(SCHEMA)
        for table, cols in MIGRATIONS.items():
            have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
            for name, typ in cols:
                if name not in have:
                    con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")


def row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for k in JSON_FIELDS & d.keys():
        if isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except json.JSONDecodeError:
                pass
    return d


def rows(rs) -> list[dict]:
    return [row(r) for r in rs]


def dumps(v) -> str:
    return json.dumps(v, separators=(",", ":"))
