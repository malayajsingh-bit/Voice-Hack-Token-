"""Which voice calls which seller: female (Sarika) or male (Rahul), chosen by results.

Both agents run the same prompt and tools. Calls start 50/50; once a voice has MIN_CALLS
audited calls in a segment (state + seller category), most calls go to the voice with the
better good-call rate there and EXPLORE of them still go to the other, so the result keeps
being checked. Good call = graded Non-Fatal by the audit."""
import random

import context
import store

AGENTS = {"female": {"agent_id": "IndiaMART-S-31ca33eb-e1ce", "name": "Sarika"},
          "male": {"agent_id": "IndiaMART-S-8c69b7be-a89a", "name": "Rahul"}}
MIN_CALLS = 5
EXPLORE = 0.2


def segment(glid):
    c = context.build(glid)
    raw = c.get("raw") or {}
    return context.state_of(raw) or "unknown", store.category_of(glid) or "unknown"


def stats(seg=None):
    """Good-call rate per voice, optionally inside one segment."""
    rows = store.rows("""SELECT c.glid, c.meta, a.grade FROM call c JOIN audit a ON a.call_id=c.id
                         WHERE c.source='live' AND a.grade IS NOT NULL""")
    out = {v: {"calls": 0, "good": 0} for v in AGENTS}
    for r in rows:
        v = store.L(r["meta"], {}).get("voice")
        if v not in out or (seg and r["glid"] and segment(r["glid"]) != seg):
            continue
        out[v]["calls"] += 1
        out[v]["good"] += r["grade"] == "Non-Fatal"
    for v in out.values():
        v["rate"] = round(v["good"] / v["calls"], 2) if v["calls"] else None
    return out


def choose(glid, rng=random):
    live = [v for v in AGENTS if AGENTS[v]["agent_id"]]
    seg = segment(glid)
    st = stats(seg)
    if len(live) == 1:
        v, why = live[0], "only one voice agent is live"
    elif any(st[v]["calls"] < MIN_CALLS for v in live):
        v = min(live, key=lambda x: (st[x]["calls"], rng.random()))
        why = f"learning: fewer than {MIN_CALLS} calls for a voice in {seg[0]} / {seg[1]}"
    else:
        best = max(live, key=lambda x: st[x]["rate"])
        other = [x for x in live if x != best][0]
        v = other if rng.random() < EXPLORE else best
        why = f"{best} wins in {seg[0]} / {seg[1]} ({st[best]['rate']:.0%} vs {st[other]['rate']:.0%})" + \
              ("; this call re-checks the other voice" if v == other else "")
    return {"voice": v, **AGENTS[v], "segment": {"state": seg[0], "category": seg[1]}, "stats": st, "why": why}
