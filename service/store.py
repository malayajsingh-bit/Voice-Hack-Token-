"""SQLite store, Postgres-compatible schema. One file, no ORM."""
import json
import sqlite3
import time
import uuid

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS seller_ctx (glid TEXT PRIMARY KEY, seller_md TEXT, persona TEXT, demand TEXT,
  raw TEXT, built_at TEXT);
CREATE TABLE IF NOT EXISTS call (id TEXT PRIMARY KEY, glid TEXT, source TEXT, started TEXT, duration REAL,
  transcript TEXT, recording_url TEXT, variant TEXT DEFAULT 'A', meta TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS audit (call_id TEXT PRIMARY KEY, grade TEXT, reason TEXT, failure_turn INTEGER,
  confidence REAL, flags TEXT, sales_ready INTEGER, sales_reason TEXT, model TEXT, cost REAL, graded_at TEXT,
  human_grade TEXT, cause_id TEXT);
CREATE TABLE IF NOT EXISTS root_cause (id TEXT PRIMARY KEY, name TEXT, description TEXT, count INTEGER,
  fatal_count INTEGER, severity REAL, impact REAL, examples TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS fix (id TEXT PRIMARY KEY, cause_id TEXT, prompt_diff TEXT, rationale TEXT,
  status TEXT DEFAULT 'proposed', created TEXT, decided_at TEXT);
CREATE TABLE IF NOT EXISTS experiment (id TEXT PRIMARY KEY, fix_id TEXT, share REAL, window_min INTEGER,
  goal TEXT, a_calls INTEGER DEFAULT 0, b_calls INTEGER DEFAULT 0, a_bad INTEGER DEFAULT 0, b_bad INTEGER DEFAULT 0,
  p_value REAL, decision TEXT, started TEXT, finished TEXT);
CREATE TABLE IF NOT EXISTS switch_log (id TEXT PRIMARY KEY, call_id TEXT, glid TEXT, t TEXT, signal TEXT,
  change TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS queue (id TEXT PRIMARY KEY, kind TEXT, call_id TEXT, glid TEXT, reason TEXT,
  payload TEXT, created TEXT, handled_by TEXT);
CREATE TABLE IF NOT EXISTS prompt_version (id TEXT PRIMARY KEY, version INTEGER, prompt TEXT, fix_id TEXT,
  created TEXT, active INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS tool_log (id TEXT PRIMARY KEY, call_id TEXT, glid TEXT, tool TEXT,
  request TEXT, response TEXT, status TEXT, created TEXT);
CREATE INDEX IF NOT EXISTS tool_log_call_idx ON tool_log(call_id);
CREATE TABLE IF NOT EXISTS interaction_map (interaction_id TEXT PRIMARY KEY, call_id TEXT, created TEXT);
"""


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def nid():
    return uuid.uuid4().hex[:10]


def db():
    config.DATA.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(config.DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def rows(sql, args=()):
    with db() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def one(sql, args=()):
    r = rows(sql, args)
    return r[0] if r else None


def run(sql, args=()):
    with db() as c:
        c.execute(sql, args)


def J(v):
    return json.dumps(v, ensure_ascii=False)


def L(s, default=None):
    try:
        return json.loads(s) if s else (default if default is not None else [])
    except Exception:
        return default if default is not None else []
