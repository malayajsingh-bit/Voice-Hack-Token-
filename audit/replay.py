"""Replay test: re-run a cluster's failed calls against the fixed prompt.

For each Fatal call in the cause: keep the SELLER's turns exactly as spoken, let an LLM
play the BOT with prompt B, re-grade the new transcript with the same grader, and report
the Fatal rate before vs after. Deterministic, offline, minutes not days — this is the
"drop in failure rate after a suggested fix is tested" number.

    python3 audit/replay.py <fix_id>"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "service"))
import config   # noqa: E402
import grade as gr   # noqa: E402
import llm      # noqa: E402
import store    # noqa: E402

BOT_MODEL = "anthropic/claude-sonnet-5"      # plays the bot in replays; reliable short completions

REPLAY = """You are the Voice Bot. Follow the SYSTEM PROMPT below exactly. You are replaying a real call:
the seller's lines are fixed; you produce only the bot's next line each time. Keep it to one or two
short sentences in the seller's language mix. If the prompt tells you to call a tool, write the
tool call as [tool: name(args)] and continue as if it returned a sensible result.

SYSTEM PROMPT:
<<<
{prompt}
>>>

CONVERSATION SO FAR:
{so_far}

Bot:"""


def split_turns(transcript):
    turns = []
    for line in transcript.splitlines():
        if ":" in line:
            who, text = line.split(":", 1)
            turns.append((who.strip().lower(), text.strip()))
    return turns


def replay_one(transcript, prompt):
    turns = split_turns(transcript)
    out, cost = [], 0.0
    for who, text in turns:
        if who.startswith("seller") or who.startswith("buyer") or who.startswith("user"):
            out.append(f"Seller: {text}")
        else:
            so_far = "\n".join(out) if out else "(call starts; greet the seller)"
            reply, c = llm.chat([{"role": "user", "content": REPLAY.format(prompt=prompt, so_far=so_far)}],
                                BOT_MODEL, max_tokens=1500, label="replay: ")
            cost += c
            out.append("Bot: " + reply.strip().split("\n")[0][:300])
    return "\n".join(out), cost


def run(fix_id, limit=10):
    f = store.one("SELECT * FROM fix WHERE id=?", (fix_id,))
    meta = store.L(f["rationale"], {})
    if not meta.get("new_prompt"):
        raise ValueError("fix has no new prompt to replay against")
    calls = store.rows("""SELECT c.id, c.transcript FROM audit a JOIN call c ON c.id=a.call_id
                          WHERE a.cause_id=? AND a.grade='Fatal' LIMIT ?""", (f["cause_id"], limit))
    before = len(calls)
    after_fatal, cost, results = 0, 0.0, []
    for c in calls:
        new_t, rc = replay_one(c["transcript"], meta["new_prompt"])
        cost += rc
        rid = f"replay-{fix_id}-{c['id']}"
        store.run("INSERT OR REPLACE INTO call VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (rid, None, "replay", store.now(), None, new_t, None, "B", store.J({"fix": fix_id, "orig": c["id"]}), store.now()))
        g = gr.grade(rid, new_t)
        cost += g.get("cost", 0)
        after_fatal += g.get("grade") == "Fatal"
        results.append({"orig": c["id"], "replay": rid, "grade": g.get("grade"), "reason": g.get("reason")})
    r = {"fix": fix_id, "n": before, "before_fatal": before, "after_fatal": after_fatal,
         "before_rate": 1.0 if before else 0, "after_rate": round(after_fatal / before, 3) if before else 0,
         "cost": round(cost, 4), "results": results, "at": store.now()}
    meta["replay"] = {k: v for k, v in r.items() if k != "results"}
    store.run("UPDATE fix SET rationale=? WHERE id=?", (store.J(meta), fix_id))
    return r


if __name__ == "__main__":
    r = run(sys.argv[1])
    print(json.dumps({k: v for k, v in r.items() if k != "results"}, indent=1))
    for x in r["results"]:
        print(" ", x["orig"], "->", x["grade"], "—", (x["reason"] or "")[:80])
