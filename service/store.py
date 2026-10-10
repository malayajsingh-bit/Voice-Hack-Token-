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
CREATE TABLE IF NOT EXISTS call_situation (call_id TEXT, situation TEXT, PRIMARY KEY (call_id, situation));
CREATE TABLE IF NOT EXISTS seller_fact (id TEXT PRIMARY KEY, glid TEXT, kind TEXT, fact TEXT, quote TEXT,
  call_id TEXT, check_data INTEGER DEFAULT 0, removed INTEGER DEFAULT 0, created TEXT);
CREATE TABLE IF NOT EXISTS tool_log (id TEXT PRIMARY KEY, call_id TEXT, glid TEXT, tool TEXT,
  request TEXT, response TEXT, status TEXT, created TEXT);
CREATE INDEX IF NOT EXISTS tool_log_glid_t ON tool_log(glid, created);
CREATE TABLE IF NOT EXISTS seller_category (key TEXT PRIMARY KEY, label TEXT, source TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS seller_category_map (glid TEXT PRIMARY KEY, category TEXT, assigned TEXT);
CREATE TABLE IF NOT EXISTS category_playbook (category TEXT PRIMARY KEY, text TEXT, fix_id TEXT, updated TEXT);
"""

# Columns added after the first release; ALTER is a no-op error when they already exist.
MIGRATIONS = [
    "ALTER TABLE root_cause ADD COLUMN level INTEGER DEFAULT 1",
    "ALTER TABLE root_cause ADD COLUMN scope TEXT",
]

# Level 2, axis 1: dispositions (what kind of conversation it was)
SITUATIONS = {
    "busy": "Busy or rushed sellers",
    "price": "Price objections",
    "not_interested": "Not interested",
    "tried_before": "Tried IndiaMART before",
    "wrong_person": "Wrong person or wrong data",
    "confused": "Confused or language mismatch",
    "engaged": "Engaged sellers (the pitch)",
    "scheduling": "Booking the meeting or callback",
}

SEED_CATEGORIES = [
    ("sanitation_plumbing", "Sanitation & plumbing"), ("hardware_tools", "Hardware & tools"),
    ("industrial_machinery", "Industrial machinery"), ("electrical_electronics", "Electrical & electronics"),
    ("chemicals_plastics", "Chemicals & plastics"), ("textiles_apparel", "Textiles & apparel"),
    ("packaging", "Packaging"), ("building_materials", "Building materials"),
    ("furniture_interiors", "Furniture & interiors"), ("food_agri", "Food & agri"),
    ("pharma_healthcare", "Pharma & healthcare"), ("auto_parts", "Auto parts"),
    ("metals_steel", "Metals & steel"), ("home_kitchen", "Home & kitchen"), ("office_supplies", "Office supplies"),
]


def categories():
    """Current category list: the seed plus any the LLM has created."""
    with db() as c:
        if not c.execute("SELECT count(*) FROM seller_category").fetchone()[0]:
            c.executemany("INSERT OR IGNORE INTO seller_category VALUES (?,?,'seed',datetime('now'))", SEED_CATEGORIES)
        return [dict(r) for r in c.execute("SELECT key, label, source FROM seller_category ORDER BY source DESC, label")]


def category_label(key):
    r = one("SELECT label FROM seller_category WHERE key=?", (key,))
    return r["label"] if r else key


def category_of(glid):
    r = one("SELECT category FROM seller_category_map WHERE glid=?", (str(glid),))
    return r["category"] if r else None


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def nid():
    return uuid.uuid4().hex[:10]


def db():
    config.DATA.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(config.DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    for m in MIGRATIONS:
        try:
            c.execute(m)
        except sqlite3.OperationalError:
            pass
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


def tool_calls_for(call_id, before_s=60, after_s=120):
    """Tool calls that belong to one call. Sarvam's tools send the seller's GLID but not our
    call id, so a tool call is matched by call id when present, else by GLID and time: from
    shortly before the call started to shortly after it ended."""
    import datetime as _dt
    c = one("SELECT glid, started, duration FROM call WHERE id=?", (call_id,))
    if not c:
        return []
    byid = rows("SELECT * FROM tool_log WHERE call_id=? ORDER BY created", (call_id,))
    if byid or not c.get("glid") or not c.get("started"):
        return byid
    fmt = "%Y-%m-%d %H:%M:%S"
    try:
        start = _dt.datetime.strptime(str(c["started"])[:19], fmt)
    except ValueError:
        return []
    lo = (start - _dt.timedelta(seconds=before_s)).strftime(fmt)
    hi = (start + _dt.timedelta(seconds=float(c.get("duration") or 600) + after_s)).strftime(fmt)
    return rows("SELECT * FROM tool_log WHERE glid=? AND created BETWEEN ? AND ? ORDER BY created",
                (str(c["glid"]), lo, hi))
