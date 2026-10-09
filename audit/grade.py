"""Step 1: grade every call. One schema-enforced call per transcript.

Fatal = the call could not achieve its purpose because of the bot (wrong facts,
loop, ignored the seller, dropped on an objection it should handle, kept pitching
after a no). Non-Fatal = reached its purpose or failed for reasons outside the bot
(not answered, wrong number, seller busy)."""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "service"))
import config   # noqa: E402
import llm      # noqa: E402
import store    # noqa: E402

FLAGS = ["wrong_requirement", "frustration", "loop", "low_confidence", "human_request",
         "kept_pitching_after_no", "missed_objection", "wrong_fact", "bad_timing", "language_mismatch"]

PROMPT = """You audit ONE outbound sales call made by IndiaMART's Voice Bot to a seller (Hinglish).
Grade the BOT, not the seller.

Fatal: the call could not achieve its purpose because of something the bot did or failed to do.
Non-Fatal: purpose achieved, or failure was outside the bot's control.

FLAGS (pick all that apply, from this list only): {flags}
sales_ready: true only if the seller showed buying intent — asked about plans, prices, results,
or agreed to a meeting/callback — AND was not irritated.

Return ONLY JSON:
{{"grade": "Fatal|Non-Fatal",
  "reason": "<one line, specific, names the turn where it went wrong>",
  "evidence": "<the exact transcript line (quoted) that shows it>",
  "failure_turn": <int turn index or -1>,
  "confidence": <0-1>,
  "flags": [...],
  "sales_ready": true|false,
  "sales_reason": "<one line or empty>",
  "persona_needed": "<rushed|frustrated|confused|interested|language_hindi|language_english|none> — what the bot should have adapted to",
  "persona_switch_seen": true|false}}

{examples}
TRANSCRIPT (turns are numbered):
{transcript}"""

EXAMPLE = """HUMAN-GRADED EXAMPLES (learn the bar from these):
{items}
"""


def fewshot(k=6):
    """Human-labelled calls (dataset labels and dashboard overrides) as graded examples."""
    rows = store.rows("""SELECT c.transcript, a.human_grade, a.reason FROM audit a JOIN call c ON c.id=a.call_id
                         WHERE a.human_grade IS NOT NULL AND c.transcript<>'' ORDER BY a.graded_at DESC LIMIT ?""", (k,))
    if not rows:
        return ""
    items = []
    for r in rows:
        t = r["transcript"].strip().splitlines()
        snippet = "\n".join(t[:6]) + ("\n…" if len(t) > 6 else "")
        items.append(f"--- {r['human_grade']}" + (f" ({r['reason']})" if r.get("reason") else "") + f"\n{snippet}")
    return EXAMPLE.format(items="\n".join(items))


def numbered(transcript):
    lines = [l for l in transcript.splitlines() if l.strip()]
    return "\n".join(f"[{i}] {l}" for i, l in enumerate(lines))


def grade(call_id, transcript, model=config.GRADE_MODEL, use_examples=True):
    j, cost = llm.chat_json([{"role": "user", "content": PROMPT.format(
        flags=", ".join(FLAGS), examples=fewshot() if use_examples else "",
        transcript=numbered(transcript)[:30000])}], model, max_tokens=1000, label="grade: ")
    flags = [f for f in (j.get("flags") or []) if f in FLAGS]
    reason = (j.get("reason") or "") + (f' — "{j["evidence"][:160]}"' if j.get("evidence") else "")
    store.run("""INSERT OR REPLACE INTO audit (call_id, grade, reason, failure_turn, confidence, flags, sales_ready,
                 sales_reason, model, cost, graded_at, human_grade, cause_id)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,
                   (SELECT human_grade FROM audit WHERE call_id=?), (SELECT cause_id FROM audit WHERE call_id=?))""",
              (call_id, j.get("grade"), reason, int(j.get("failure_turn") or -1),
               float(j.get("confidence") or 0), store.J(flags), 1 if j.get("sales_ready") else 0,
               j.get("sales_reason") or "", model, cost, store.now(), call_id, call_id))
    for f in flags:
        if f in ("frustration", "human_request", "loop", "wrong_requirement", "low_confidence"):
            store.run("INSERT INTO queue VALUES (?,?,?,?,?,?,?,?)",
                      (store.nid(), "risk", call_id, None, f, store.J({"from": "audit"}), store.now(), None))
    j["cost"] = cost
    return j


def agreement():
    """Auto vs human grades where humans labelled. Returns counts, precision/recall for Fatal, kappa."""
    r = store.rows("SELECT grade, human_grade FROM audit WHERE human_grade IS NOT NULL AND grade IS NOT NULL")
    n = len(r)
    if not n:
        return {"n": 0}
    tp = sum(1 for x in r if x["grade"] == "Fatal" and x["human_grade"] == "Fatal")
    fp = sum(1 for x in r if x["grade"] == "Fatal" and x["human_grade"] != "Fatal")
    fn = sum(1 for x in r if x["grade"] != "Fatal" and x["human_grade"] == "Fatal")
    tn = n - tp - fp - fn
    po = (tp + tn) / n
    pa = ((tp + fp) * (tp + fn) + (fn + tn) * (fp + tn)) / (n * n)
    kappa = (po - pa) / (1 - pa) if pa < 1 else 1.0
    return {"n": n, "accuracy": round(po, 3), "kappa": round(kappa, 3),
            "fatal_precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "fatal_recall": round(tp / (tp + fn), 3) if tp + fn else None,
            "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn}}
