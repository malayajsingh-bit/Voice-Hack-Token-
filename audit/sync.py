#!/usr/bin/env python3
"""Pull finished calls from Sarvam analytics into our store, then grade them.

    python3 audit/sync.py [--hours 24]

The webhook on /calls/ingest is the push path; this is the pull path for calls that
were placed from the console or a campaign without our webhook."""
import argparse
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent / "service"))
import grade as gr   # noqa: E402
import sarvam        # noqa: E402
import store         # noqa: E402


def to_text(tr):
    """Normalise whatever shape the transcript endpoint returns to 'Bot: …\\nSeller: …'."""
    if isinstance(tr, str):
        return tr
    items = tr.get("messages") or tr.get("turns") or tr.get("transcript") or tr.get("items") or []
    if isinstance(items, str):
        return items
    out = []
    for m in items:
        role = (m.get("role") or m.get("speaker") or m.get("sender") or "?").lower()
        who = "Bot" if role in ("agent", "assistant", "bot", "ai") else "Seller"
        out.append(f"{who}: {m.get('text') or m.get('content') or m.get('message') or ''}")
    return "\n".join(out)


def sync(hours=24, grade_new=True):
    page = sarvam.interactions(hours=hours)
    items = page.get("items") or page.get("data") or []
    n = 0
    for it in items:
        iid = it.get("interaction_id")
        if not iid or store.one("SELECT id FROM call WHERE id=? AND transcript<>''", (iid,)):
            continue
        try:
            text = to_text(sarvam.transcript(iid))
        except Exception as e:
            print("  transcript failed", iid, str(e)[:100]); continue
        av = it.get("agent_variables") or {}
        store.run("INSERT OR REPLACE INTO call VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (iid, str(av.get("glid") or ""), "live", it.get("start_datetime"), it.get("duration_in_seconds"),
                   text, None, av.get("variant", "A"), store.J(it), store.now()))
        n += 1
        if grade_new and text:
            j = gr.grade(iid, text)
            print(f"  {iid}: {j.get('grade')} — {j.get('reason', '')[:70]}")
    print(f"synced {n} new calls of {len(items)} listed")
    return n


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--no-grade", action="store_true")
    a = ap.parse_args()
    sync(a.hours, not a.no_grade)
