"""What the Sarvam agent calls mid-call. Every function here must answer in well
under a second: it reads the cache and logs; it never computes or fetches."""
import time

import context
import store

CALLBACK_DEDUP_SECONDS = 120   # the live bot booked the same callback twice in one call (docs/call-log.md)
NO_DATA_SAY = ("Ji, aapke product ka recent data abhi mere paas nahi hai. "
               "Main aapko WhatsApp par proposal bhej rahi hoon, aur hamare executive aakar sab detail mein samjha denge.")
CLOSING = "Aapka samay dene ke liye dhanyavaad, aapke dhande mein khoob tarakki ho!"
PROPOSAL_LINE = ("Main aapko WhatsApp par proposal bhej rahi hoon, ek baar dekh lijiye. "
                 "Aur hamare executive aakar aapko sab detail mein samjha denge.")

# Engagement checkpoint: the bot reports what the seller did, the server scores it, so the
# meeting-only vs pre-sales decision is the same on every call and the audit can check it.
ENGAGEMENT = {"asked_question": 2, "detailed_answers": 2, "shared_pain": 1, "positive_tone": 1,
              "one_word_answers": -1, "burned_before": -1, "busy_or_irritated": -3}
PRESALES_AT = 3


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
    out = {"state": "presales", "ask_permission": "Ji, kya aapke paas do minute aur hain? Aapko batati hoon IndiaMART aapke liye kya kar sakta hai.",
           "close": "Toh hamare executive aapse kab mil sakte hain, kal ya parson?"}
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
            "assumption": f"{d.get('weekly_buyleads')} buyer requirements/week, 1 in {int(1 / close)} orders",
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
    # the team sends the proposal on WhatsApp after every booking; the bot only says it is coming
    store.run("INSERT INTO queue VALUES (?,?,?,?,?,?,?,?)",
              (store.nid(), "proposal", call_id, str(glid), "send proposal on WhatsApp", store.J({"when": when}),
               store.now(), None))
    meeting = "meet" in (note or "").lower()
    return {"ok": True, "duplicate": False,
            "say": (f"Theek hai, {when} hamare executive aapse milenge." if meeting
                    else f"Theek hai, {when} par call karte hain.") + " " + PROPOSAL_LINE,
            "closing": CLOSING}


def assess_engagement(glid, call_id, signals, asked_price=False):
    """signals: the names in ENGAGEMENT the bot saw. asked_price: the seller asked price or plans himself."""
    seen = [x for x in (signals or []) if x in ENGAGEMENT]
    score = sum(ENGAGEMENT[x] for x in seen)
    blocked = "busy_or_irritated" in seen
    if asked_price and not blocked:
        path, why = "presales", "seller asked about price or plans himself"
    elif score >= PRESALES_AT and not blocked:
        path, why = "presales", f"score {score} >= {PRESALES_AT}"
    else:
        path, why = "meeting_only", ("busy or irritated" if blocked else f"score {score} < {PRESALES_AT}")
    store.run("INSERT INTO switch_log VALUES (?,?,?,?,?,?,?)",
              (store.nid(), call_id, str(glid), store.now(), "engagement:" + path,
               store.J({"score": score, "signals": seen, "asked_price": bool(asked_price)}), why))
    product = context.product_of(context.build(glid).get("raw") or {}) if glid else "aapka saaman"
    if path == "presales":
        instruction = ("Offer the meeting, then ask: do minute aur hain? If no, fix the meeting. If yes, explain "
                       "how IndiaMART works for this seller, then fix the meeting.")
        say = ("Ji, hamare executive aapse milkar sab detail mein dikha denge. "
               "Kya aapke paas do minute aur hain? Bataun IndiaMART aapke liye kya kar sakta hai?")
    else:
        instruction = "Do not explain or pitch. Fix the meeting with the executive: day, time, place."
        say = (f"Ji, hamare IndiaMART executive aakar dikha denge ki {product} ke buyers aap tak kaise pahunchenge. "
               "Kal ya parson, kab mil sakte hain?")
    # the spoken line is fixed here, so both paths sound the same on every call
    return {"sales_path": path, "score": score, "why": why, "instruction": instruction, "say": say}


def flag_risk(glid, call_id, kind, note=""):
    store.run("INSERT INTO queue VALUES (?,?,?,?,?,?,?,?)",
              (store.nid(), "risk", call_id, str(glid), kind, store.J({"note": note}), store.now(), None))
    n = store.one("SELECT count(*) AS n FROM queue WHERE kind='risk' AND call_id=?", (call_id,))["n"]
    return {"ok": True, "escalate": n >= 2 or kind == "human_request",
            "instruction": "Transfer to a human now." if (n >= 2 or kind == "human_request")
            else "Continue carefully; one more problem and transfer."}
