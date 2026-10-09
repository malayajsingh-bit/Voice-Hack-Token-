"""What the Sarvam agent calls mid-call. Every function here must answer in well
under a second: it reads the cache and logs; it never computes or fetches."""
import context
import store

PERSONA_MOVES = {
    "rushed":        {"pace": "fast", "tone": "crisp", "plan": "Give the 2-minute version. Offer a callback at a time they choose. No pitch unless asked."},
    "frustrated":    {"pace": "slow", "tone": "calm, apologetic once", "plan": "Acknowledge, drop the pitch, ask one question about what went wrong. If still angry, close politely."},
    "confused":      {"pace": "slow", "tone": "simple words, one idea per sentence", "plan": "Restate in Hindi, one benefit, check understanding."},
    "interested":    {"pace": "medium", "tone": "warm, confident", "plan": "Ask about their current enquiries; move to flag_sales_ready when they ask about plans, prices or results."},
    "language_hindi":   {"pace": "same", "tone": "same", "plan": "Switch fully to Hindi; keep product words in English."},
    "language_english": {"pace": "same", "tone": "same", "plan": "Switch to English; keep it simple."},
    "silence":       {"pace": "medium", "tone": "gentle", "plan": "Check if they are there; offer to call back."},
}


def set_persona(glid, call_id, signal, note=""):
    move = PERSONA_MOVES.get(signal) or {"pace": "medium", "tone": "neutral", "plan": "Continue."}
    store.run("INSERT INTO switch_log VALUES (?,?,?,?,?,?,?)",
              (store.nid(), call_id, str(glid), store.now(), signal, store.J(move), note))
    return {"signal": signal, **move,
            "instruction": f"From now on: pace {move['pace']}, tone {move['tone']}. {move['plan']}"}


def flag_sales_ready(glid, call_id, reason, confidence=0.7):
    ok = float(confidence or 0) >= 0.7
    if ok:
        c = context.build(glid)
        store.run("INSERT INTO queue VALUES (?,?,?,?,?,?,?,?)",
                  (store.nid(), "presales", call_id, str(glid), reason,
                   store.J({"confidence": confidence, "demand": c["demand"]}), store.now(), None))
        return {"state": "presales", "ask_permission": "Sir, do aur minute hain? Aapki category ka ek number batata/batati hoon.",
                "pitch": c["demand"]["line"], "close": "Meeting fix karein ya proposal WhatsApp par bhej doon?"}
    return {"state": "discover", "instruction": "Not enough signal yet. Keep discovering; do not pitch."}


def get_demand_pitch(glid):
    c = context.build(glid)
    d = c["demand"]
    return {"buyers_month": d["buyers_month"], "business_month": d["value_month_h"], "aov": d["aov_h"],
            "buyleads_city_30d": d["bl_city_30d"], "could_make_month": d["monthly_est_h"],
            "assumption": f"{d['weekly_buyleads']} BuyLeads/week, 1 in {int(1/d['close_rate'])} closes",
            "say": d["line"]}


def book_callback(glid, call_id, when, note=""):
    store.run("INSERT INTO queue VALUES (?,?,?,?,?,?,?,?)",
              (store.nid(), "callback", call_id, str(glid), f"callback {when}", store.J({"note": note}),
               store.now(), None))
    return {"ok": True, "say": f"Theek hai, {when} par call karte hain. Proposal WhatsApp par bhej raha/rahi hoon."}


def flag_risk(glid, call_id, kind, note=""):
    store.run("INSERT INTO queue VALUES (?,?,?,?,?,?,?,?)",
              (store.nid(), "risk", call_id, str(glid), kind, store.J({"note": note}), store.now(), None))
    n = store.one("SELECT count(*) AS n FROM queue WHERE kind='risk' AND call_id=?", (call_id,))["n"]
    return {"ok": True, "escalate": n >= 2 or kind == "human_request",
            "instruction": "Transfer to a human now." if (n >= 2 or kind == "human_request")
            else "Continue carefully; one more problem and transfer."}
