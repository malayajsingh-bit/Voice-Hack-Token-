"""What the Sarvam agent calls mid-call. Every function here must answer in well
under a second: it reads the cache and the tool log; it never computes, fetches,
or calls an LLM. Pre-call context (seller_md, persona, demand) must already be in
seller_ctx — context.build populated it at /calls/start time."""
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

CALLBACK_DEDUP_SECONDS = 120   # see 'duplicate bookings' in docs/call-log.md


def _cached(glid):
    """Return the cached pre-call context for this GLID, or None. Tools never build
    context mid-call — building invokes an LLM for the persona (seconds, not ms)
    and would race with the live turn."""
    row = store.one("SELECT seller_md, persona, demand FROM seller_ctx WHERE glid=?", (str(glid),))
    if not row:
        return None
    return {"seller_md": row["seller_md"], "persona": store.L(row["persona"], {}),
            "demand": store.L(row["demand"], {})}


def get_seller_context(glid):
    c = _cached(glid)
    if not c:
        return {"available": False,
                "say": "Sir, aapke details abhi load nahi ho paye. Hamara team WhatsApp par proposal bhejega."}
    return {"available": True, "seller_md": c["seller_md"], "persona": c["persona"],
            "hook": (c.get("demand") or {}).get("line", "")}


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
    c = _cached(glid)
    demand = (c or {}).get("demand") or {}
    store.run("INSERT INTO queue VALUES (?,?,?,?,?,?,?,?)",
              (store.nid(), "presales", call_id, str(glid), reason,
               store.J({"confidence": confidence, "demand": demand}), store.now(), None))
    pitch = demand.get("line") or ""
    out = {"state": "presales",
           "ask_permission": "Sir, do aur minute hain? Aapki category ka ek number batata/batati hoon.",
           "close": "Meeting fix karein ya proposal WhatsApp par bhej doon?"}
    if pitch:
        out["pitch"] = pitch
    else:
        out["available"] = False
        out["say"] = ("Sir, aapki category ka recent data abhi mere paas nahi hai. "
                      "Hamara team WhatsApp par proposal bhejega.")
    return out


def get_demand_pitch(glid):
    c = _cached(glid)
    d = (c or {}).get("demand") or {}
    # No seller, or demand numbers are all empty -> never improvise '0 buyers, 0 rupaye'.
    numeric = [d.get("buyers_month"), d.get("value_month"), d.get("aov"), d.get("bl_city_30d"), d.get("monthly_est")]
    if not d or not any(float(x or 0) > 0 for x in numeric):
        return {"available": False,
                "say": ("Sir, aapki category ka recent data abhi mere paas nahi hai. "
                        "Hamara team WhatsApp par proposal bhejega.")}
    return {"available": True,
            "buyers_month": d.get("buyers_month"), "business_month": d.get("value_month_h"),
            "aov": d.get("aov_h"), "buyleads_city_30d": d.get("bl_city_30d"),
            "could_make_month": d.get("monthly_est_h"),
            "assumption": f"{d.get('weekly_buyleads')} BuyLeads/week, "
                          f"1 in {int(1/d['close_rate']) if d.get('close_rate') else 10} closes",
            "say": d.get("line")}


def book_callback(glid, call_id, when, note=""):
    """Dedupe within CALLBACK_DEDUP_SECONDS for the same GLID: the live bot has twice
    (phone call 4 in docs/call-log.md) called book_callback a second time while the
    seller confirmed the time back. Repeating it creates two queue rows."""
    recent = store.one("SELECT id, created FROM queue WHERE kind='callback' AND glid=? "
                       "AND created >= datetime('now', ?) ORDER BY created DESC",
                       (str(glid), f"-{CALLBACK_DEDUP_SECONDS} seconds"))
    if recent:
        return {"ok": True, "duplicate": True, "existing_id": recent["id"],
                "say": f"Theek hai, {when} par call confirm hai. Proposal WhatsApp par bhej raha/rahi hoon."}
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
