#!/usr/bin/env python3
"""Start a clean round: back up the database, clear calls, audits, problems, fixes and
seller memory, and record the prompt now on Sarvam as the active version. Seller context and
categories are kept, so no LLM work is repeated.

    python3 audit/demo_reset.py"""
import pathlib
import shutil
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "service"))
import config   # noqa: E402
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fix as fx    # noqa: E402
import store    # noqa: E402

CLEAR = ["call", "audit", "root_cause", "fix", "experiment", "switch_log", "queue", "prompt_version",
         "call_situation", "seller_fact", "tool_log", "category_playbook"]

if __name__ == "__main__":
    bak = config.DATA / f"audit_backup_{time.strftime('%Y%m%d_%H%M%S')}.db"
    shutil.copy(config.DB, bak)
    with store.db() as c:
        for t in CLEAR:
            c.execute(f"DELETE FROM {t}")
    store.run("INSERT INTO prompt_version VALUES (?,?,?,?,?,1)",
              (store.nid(), 1, fx.PROMPT_FILE.read_text(encoding="utf-8"), None, store.now()))
    print(f"backup: {bak.name}; cleared {len(CLEAR)} tables; active prompt = {fx.PROMPT_FILE.name}")
