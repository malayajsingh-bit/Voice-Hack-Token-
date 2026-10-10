"""Rule-based checks that run BEFORE the LLM grader.

The LLM grader missed obvious failures twice (see docs/call-log.md: '32 hazaar'
spoken when the tool had returned 31,000). These rules catch the mechanical ones
by diffing what the bot said against what tools returned, so a mismatch is Fatal
with no inference and no cost.

Each rule returns a list of findings:
    {grade: 'Fatal' | None, flag: str, reason: str, evidence: str}

`check(call_id, transcript)` composes the rules and returns:
    {grade: 'Fatal' | None, reason, flags, evidence, findings}
It reads tool_log from the store; pass tool_log=... to override (for tests)."""
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "service"))
import store   # noqa: E402

UNITS = {"crore": 10_000_000, "karod": 10_000_000, "karor": 10_000_000, "cr": 10_000_000,
         "lakh": 100_000, "lac": 100_000,
         "hazaar": 1000, "hazar": 1000, "hajar": 1000, "thousand": 1000, "k": 1000}

_NUM = r"\d+(?:\.\d+)?"
_UNIT = r"(?:crore|karod|karor|cr|lakh|lac|hazaar|hazar|hajar|thousand|k)"

_UNIT_RE = re.compile(rf"({_NUM})\s*({_UNIT})\b", re.IGNORECASE)
_COMMA_INT_RE = re.compile(r"\b\d{1,3}(?:,\d{2,3})+\b")
_PLAIN_INT_RE = re.compile(r"\b\d{3,}\b")                                  # >= 100
_COMPOUND_RE = re.compile(rf"({_NUM})\s*({_UNIT})\s+({_NUM})\s*({_UNIT})\b", re.IGNORECASE)

BOOKING_PHRASES = [
    "book kar", "booked", "fix kar", "schedule kar", "scheduled",
    "callback book", "callback schedule", "callback fix", "meeting fix",
    "meeting book", "call set", "set kar", "note kar li", "note kar liya",
]
CLOSING_PHRASES = [
    "dhanyawad", "shukriya", "thank you", "thanks", "aapka din", "alvida",
    "khuda hafiz", "goodbye", "bye", "call rakh", "end karti", "ending the",
    "rakhti hoon", "rakhta hoon", "namaskar",
]
PITCH_WORDS = [
    "trustseal", "buyleads", "buylead", "plan", "subscribe", "proposal",
    "crore", "karod", "lakh", "hazaar", "rupaye", "rupees",
    "per year", "mahine", "paisa", "offer", "upgrade", "mdc", "pns",
]
CLEAR_NO = [
    "nahi chahiye", "nahi lena", "nahi karna", "interested nahi",
    "mat karo", "mat bhejna", "no thanks", "not interested", "mujhe nahi",
    "band karo", "bas karo", "call mat", "nahin chahiye",
]


# ---------------------------------------------------------------- parsing ---
def _canon(num_str, unit=None):
    try:
        n = float(num_str)
    except ValueError:
        return None
    if unit:
        n *= UNITS.get(unit.lower(), 1)
    return int(round(n))


def extract_numbers(text):
    """All numeric values mentioned, canonicalised to integer paise units.
    '31,000' -> 31000 ; '32 hazaar' -> 32000 ; '5 crore 21 lakh' -> 52_100_000."""
    text = text or ""
    out = set()
    for m in _COMPOUND_RE.finditer(text):
        a = _canon(m.group(1), m.group(2))
        b = _canon(m.group(3), m.group(4))
        if a is not None and b is not None:
            out.add(a + b)
    for m in _UNIT_RE.finditer(text):
        v = _canon(m.group(1), m.group(2))
        if v is not None:
            out.add(v)
    for m in _COMMA_INT_RE.finditer(text):
        v = _canon(m.group(0).replace(",", ""))
        if v is not None:
            out.add(v)
    for m in _PLAIN_INT_RE.finditer(text):
        v = _canon(m.group(0))
        if v is not None:
            out.add(v)
    return out


def _walk(obj, acc):
    if isinstance(obj, dict):
        for v in obj.values():
            _walk(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _walk(v, acc)
    elif isinstance(obj, (int, float)):
        if obj >= 1:                                                       # ignore 0 and fractional close rates
            acc.add(int(round(float(obj))))
    elif isinstance(obj, str):
        acc |= extract_numbers(obj)


def tool_numbers(tool_log):
    """All canonical numbers any tool ever returned in this call."""
    acc = set()
    for row in tool_log or []:
        _walk(store.L(row.get("response"), {}), acc)
    return acc


def split_turns(transcript):
    """[(who, text), ...] — who is 'bot' or 'seller' or 'other'. Multi-line
    speech after a role tag belongs to that role until the next tag."""
    turns, who = [], None
    buf = []
    for raw in (transcript or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        head = line.split(":", 1)
        if len(head) == 2 and 1 <= len(head[0]) <= 30 and head[0].strip().isalpha():
            if who is not None:
                turns.append((who, " ".join(buf).strip()))
            w = head[0].strip().lower()
            who = "bot" if w in ("bot", "agent", "assistant", "ai") else \
                  "seller" if w in ("seller", "buyer", "user", "customer") else "other"
            buf = [head[1].strip()]
        else:
            buf.append(line)
    if who is not None:
        turns.append((who, " ".join(buf).strip()))
    return turns


def _contains_any(text, phrases):
    low = (text or "").lower()
    return any(p in low for p in phrases)


# ------------------------------------------------------------- the rules ---
def rule_numbers(turns, tool_log):
    """Any BOT-spoken number must match a number a tool returned, or a number the
    seller mentioned earlier. If not, it is an improvised figure — Fatal.
    Only values >= 500 are checked, so turn counts, times, and small quantities
    don't trip the rule."""
    allowed = tool_numbers(tool_log)
    findings = []
    seller_said = set()
    for who, text in turns:
        if who == "seller":
            seller_said |= extract_numbers(text)
            continue
        if who != "bot":
            continue
        for n in extract_numbers(text):
            if n < 500:
                continue
            # tolerance: within 2% matches
            if any(abs(n - a) <= max(1, int(a * 0.02)) for a in allowed | seller_said):
                continue
            findings.append({"grade": "Fatal", "flag": "wrong_fact",
                             "reason": f"bot spoke the number {n} which no tool returned "
                                       f"(and the seller never said)",
                             "evidence": text[:200]})
            break
    return findings


def rule_booked_without_tool(turns, tool_log):
    booked = any(r.get("tool") == "book_callback" and
                 store.L(r.get("response"), {}).get("ok") for r in tool_log or [])
    for who, text in turns:
        if who == "bot" and _contains_any(text, BOOKING_PHRASES) and not booked:
            return [{"grade": "Fatal", "flag": "wrong_fact",
                     "reason": "bot said a callback was booked but book_callback was never called",
                     "evidence": text[:200]}]
    return []


def rule_book_and_close_same_turn(turns, tool_log):
    """Phone call 2 and 4 from docs/call-log.md: the bot said the booking and
    then closed in the same turn, so the platform ran only the close and the
    booking was lost."""
    for who, text in turns:
        if who == "bot" and _contains_any(text, BOOKING_PHRASES) and _contains_any(text, CLOSING_PHRASES):
            return [{"grade": "Fatal", "flag": "wrong_fact",
                     "reason": "bot booked and closed in the same turn — the booking is lost",
                     "evidence": text[:220]}]
    return []


def rule_repeated_sentence(turns, _tool_log):
    seen = {}
    for who, text in turns:
        if who != "bot":
            continue
        for sent in re.split(r"[.!?]\s+", text):
            s = sent.strip().lower()
            if len(s) < 20:
                continue
            seen[s] = seen.get(s, 0) + 1
            if seen[s] >= 2:
                return [{"grade": None, "flag": "loop",
                         "reason": "bot repeated the same sentence",
                         "evidence": sent[:200]}]
    return []


def rule_pitch_after_no(turns, _tool_log):
    said_no_at = None
    for i, (who, text) in enumerate(turns):
        if who == "seller" and _contains_any(text, CLEAR_NO):
            said_no_at = i
            continue
        if said_no_at is not None and who == "bot" and _contains_any(text, PITCH_WORDS):
            return [{"grade": "Fatal", "flag": "kept_pitching_after_no",
                     "reason": "bot kept pitching after the seller said no",
                     "evidence": text[:200]}]
    return []


RULES = [rule_numbers, rule_booked_without_tool, rule_book_and_close_same_turn,
         rule_repeated_sentence, rule_pitch_after_no]


# ----------------------------------------------------------------- entry ---
def check(call_id, transcript, tool_log=None):
    if tool_log is None:
        tool_log = store.rows("SELECT tool, request, response, status, created FROM tool_log "
                              "WHERE call_id=? ORDER BY created", (call_id,))
    turns = split_turns(transcript)
    findings = []
    for r in RULES:
        try:
            findings.extend(r(turns, tool_log))
        except Exception as e:
            findings.append({"grade": None, "flag": "rule_error",
                             "reason": f"{r.__name__} crashed: {str(e)[:120]}", "evidence": ""})
    fatal = [f for f in findings if f.get("grade") == "Fatal"]
    verdict = {"grade": "Fatal" if fatal else None,
               "reason": "; ".join(f["reason"] for f in fatal) if fatal
                         else "; ".join(f["reason"] for f in findings) if findings else "",
               "evidence": fatal[0]["evidence"] if fatal else (findings[0]["evidence"] if findings else ""),
               "flags": sorted({f["flag"] for f in findings if f.get("flag")}),
               "findings": findings}
    return verdict
