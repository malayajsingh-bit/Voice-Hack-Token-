"""What the Sarvam agent calls mid-call. Every function here must answer in well
under a second: it reads the cache and logs; it never computes or fetches."""
import time

import context
import store

CALLBACK_DEDUP_SECONDS = 120   # the live bot booked the same callback twice in one call (docs/call-log.md)
NO_DATA_SAY = ("Sir, aapki category ka recent data abhi mere paas nahi hai. "
               "Hamari team WhatsApp par proposal bhejegi.")


def _has_numbers(d):
    """True when the demand block has at least one real figure (never pitch zeros)."""
    keys = ("buyers_month", "value_month", "aov", "bl_city_30d", "monthly_est")
    return any(float((d or {}).get(k) or 0) > 0 for k in keys)

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
    if not ok:
        return {"state": "discover", "instruction": "Not enough signal yet. Keep discovering; do not pitch."}
    c = context.build(glid)
    demand = c.get("demand") or {}
    store.run("INSERT INTO queue VALUES (?,?,?,?,?,?,?,?)",
              (store.nid(), "presales", call_id, str(glid), reason,
               store.J({"confidence": confidence, "demand": demand}), store.now(), None))
    out = {"state": "presales", "ask_permission": "Sir, do aur minute hain? Aapki category ka ek number batata/batati hoon.",
           "close": "Meeting fix karein ya proposal WhatsApp par bhej doon?"}
    if _has_numbers(demand) and demand.get("line"):
        out["pitch"] = demand["line"]
    else:
        out["available"] = False
        out["say"] = NO_DATA_SAY
    return out


def get_demand_pitch(glid):
    d = context.build(glid).get("demand") or {}
    if not _has_numbers(d):
        # no seller data, or every figure is zero: say so instead of quoting "0 buyers, 0 rupaye"
        return {"available": False, "say": NO_DATA_SAY}
    close = d.get("close_rate") or 0.1
    return {"available": True, "buyers_month": d.get("buyers_month"), "business_month": d.get("value_month_h"),
            "aov": d.get("aov_h"), "buyleads_city_30d": d.get("bl_city_30d"),
            "could_make_month": d.get("monthly_est_h"),
            "assumption": f"{d.get('weekly_buyleads')} BuyLeads/week, 1 in {int(1 / close)} closes",
            "say": d.get("line")}


def book_callback(glid, call_id, when, note=""):
    """One booking per seller per CALLBACK_DEDUP_SECONDS. A repeat inside the window (the bot
    re-calling the tool while confirming the time) is acknowledged but not queued again."""
    cutoff = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - CALLBACK_DEDUP_SECONDS))
    recent = store.one("SELECT id FROM queue WHERE kind='callback' AND glid=? AND created >= ? "
                       "ORDER BY created DESC", (str(glid), cutoff))
    if recent:
        return {"ok": True, "duplicate": True, "existing_id": recent["id"],
                "say": f"Theek hai, {when} confirm hai."}
    store.run("INSERT INTO queue VALUES (?,?,?,?,?,?,?,?)",
              (store.nid(), "callback", call_id, str(glid), f"callback {when}", store.J({"note": note}),
               store.now(), None))
    return {"ok": True, "duplicate": False,
            "say": f"Theek hai, {when} par call karte hain. Proposal WhatsApp par bhej raha/rahi hoon."}


def flag_risk(glid, call_id, kind, note=""):
    store.run("INSERT INTO queue VALUES (?,?,?,?,?,?,?,?)",
              (store.nid(), "risk", call_id, str(glid), kind, store.J({"note": note}), store.now(), None))
    n = store.one("SELECT count(*) AS n FROM queue WHERE kind='risk' AND call_id=?", (call_id,))["n"]
    return {"ok": True, "escalate": n >= 2 or kind == "human_request",
            "instruction": "Transfer to a human now." if (n >= 2 or kind == "human_request")
            else "Continue carefully; one more problem and transfer."}
