"""Pre-call context: seller.md + persona spec + demand pitch, per GLID, cached.

Source order: data/sellers/<glid>.json (the hackathon's provided seller files) ->
the proposal project's live fetch (Redash, needs VPN) -> a minimal stub so a call
can still be placed. Everything the bot sees is built here, before the call."""
import json
import pathlib
import sys

import config
import llm
import store

SELLERS = config.DATA / "sellers"
PROPOSAL = pathlib.Path("/mnt/c/Users/Imart/Desktop/Issues/proposal")


def load_raw(glid):
    f = SELLERS / f"{glid}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    if (PROPOSAL / "app.py").exists():
        try:
            sys.path.insert(0, str(PROPOSAL))
            import warnings; warnings.filterwarnings("ignore")
            import app as pa, deck3
            d = deck3.enrich(pa.fetch(int(glid)))
            d.pop("products", None)
            return json.loads(json.dumps(d, default=str))
        except Exception as e:
            return {"glid": glid, "company_name": f"Seller {glid}", "error": str(e)[:200]}
    return {"glid": glid, "company_name": f"Seller {glid}"}


def crore(v):
    v = float(v or 0)
    if v >= 1e7:
        return f"{v/1e7:.1f} Cr"
    if v >= 1e5:
        return f"{v/1e5:.0f} Lakh"
    return f"{v:,.0f}"


def spoken(v):
    """Amounts the voice cannot misread: '5 crore 21 lakh', never '5.2 Cr'
    (the model voiced 5.2 Cr as 'saadhe paanch crore' in testing)."""
    v = int(round(float(v or 0)))
    cr, rest = divmod(v, 10_000_000)
    lk, rest = divmod(rest, 100_000)
    th = rest // 1000
    parts = []
    if cr:
        parts.append(f"{cr} crore")
    if lk:
        parts.append(f"{lk} lakh")
    if not cr and th:
        parts.append(f"{th} hazaar")
    return " ".join(parts) or f"{v}"


def demand_pitch(raw):
    """Numbers the bot may quote in the pre-sales state. Computed, not improvised."""
    ind = raw.get("india") or {}
    bl = raw.get("bl") or {}
    aov = float(ind.get("aov") or raw.get("aov") or 0)
    buyers = int(ind.get("buyers") or raw.get("buyers_month") or 0)
    value = float(ind.get("value") or raw.get("value_month") or 0)
    posted = int(bl.get("posted_30d") or raw.get("bl_city_30d") or 0)
    weekly = 21                                   # TrustSEAL Pro BuyLeads/week
    close = 0.10                                  # conservative 1-in-10
    monthly_est = weekly * 4 * close * aov
    return {"buyers_month": buyers, "value_month": value, "value_month_h": crore(value),
            "aov": aov, "aov_h": f"{aov:,.0f}", "bl_city_30d": posted,
            "weekly_buyleads": weekly, "close_rate": close,
            "monthly_est": monthly_est, "monthly_est_h": crore(monthly_est),
            "line": (f"Aapki category mein har mahine {buyers:,} buyers aate hain, "
                     f"{spoken(value)} rupaye ka business. Average order {spoken(aov)} rupaye. "
                     f"{weekly} BuyLeads har hafte par 10 mein se 1 bhi close ho toh "
                     f"{spoken(monthly_est)} rupaye mahine ka extra business.")}


PERSONA_PROMPT = """You design the voice persona for ONE outbound sales call to an Indian MSME seller.
Decide from the seller data only; be specific; one choice per field.

SELLER DATA:
{raw}

Return ONLY JSON:
{{"language": "hinglish-hindi-leaning | hinglish-english-leaning | hindi | english",
  "formality": "respectful-informal | formal",
  "pace": "slow | medium | fast",
  "warmth": "high | medium",
  "voice": {{"gender": "female | male", "accent": "delhi | mumbai | neutral"}},
  "opening": "<one sentence, uses the seller's name and category hook>",
  "playbook": {{"price": "<one line>", "no_time": "<one line>", "not_interested": "<one line>",
               "already_tried": "<one line>"}},
  "avoid": ["<thing not to do with this seller>"],
  "why": "<two lines: which data drove these choices>"}}"""


def seller_md(raw):
    cats = [c.get("category") for c in (raw.get("cats") or []) if c.get("category")]
    ind = raw.get("india") or {}
    bl = raw.get("bl") or {}
    lines = [
        f"name: {raw.get('company_name')} · {raw.get('city') or ''} · {raw.get('customer_type') or ''}",
        f"deals_in: {', '.join(cats[:4])}" + (f" (+{(raw.get('total_mcats') or len(cats)) - 4})"
                                              if (raw.get('total_mcats') or 0) > 4 else ""),
        f"products: {raw.get('product_count') or '?'} · catalogue score {raw.get('cqs') or '?'}",
        f"enquiries_90d: {raw.get('enq_received_90d') or 0} · pns_calls_90d: {raw.get('pns_received_90d') or 0}",
        f"demand: {int(ind.get('buyers') or 0):,} buyers/month · ₹{crore(ind.get('value'))} · AOV ₹{float(ind.get('aov') or 0):,.0f}",
        f"buyleads_city_30d: {bl.get('posted_30d') or 0}",
    ]
    if raw.get("last_call"):
        lines.append(f"last_call: {raw['last_call']}")
    if raw.get("objections"):
        lines.append(f"objections_seen: {', '.join(raw['objections'][:4])}")
    if raw.get("turnover") or raw.get("annual_turnover_slab"):
        lines.append(f"turnover: {raw.get('turnover') or raw.get('annual_turnover_slab')}")
    return "\n".join(lines)


CATEGORY_PROMPT = """Place this IndiaMART seller in ONE category from the list, by what he sells.
If none fits well, create a new broad category (2 to 4 words, like the others), never a narrow product name.

CATEGORIES: {cats}

SELLER: {name}. Products and categories: {products}

Return ONLY JSON: {{"key": "<existing key, or a new snake_case key>", "label": "<label>", "new": true|false}}"""


def assign_category(glid, raw):
    """One LLM call per seller, the first time we see him. Creates a category when none fits."""
    have = store.category_of(glid)
    if have:
        return have
    cats = store.categories()
    products = ", ".join(c.get("category", "") for c in (raw.get("cats") or [])) or raw.get("company_name", "")
    try:
        j, _ = llm.chat_json([{"role": "user", "content": CATEGORY_PROMPT.format(
            cats="; ".join(f"{c['key']} = {c['label']}" for c in cats), name=raw.get("company_name"),
            products=products)}], config.FIX_MODEL, max_tokens=1500, label="category: ")
    except Exception:
        return None
    key = (j.get("key") or "").strip().lower().replace(" ", "_")[:40]
    if not key:
        return None
    if key not in {c["key"] for c in cats}:
        store.run("INSERT OR IGNORE INTO seller_category VALUES (?,?,'llm',?)", (key, j.get("label") or key, store.now()))
    store.run("INSERT OR REPLACE INTO seller_category_map VALUES (?,?,?)", (str(glid), key, store.now()))
    return key


def build(glid, force=False):
    row = store.one("SELECT * FROM seller_ctx WHERE glid=?", (str(glid),))
    if row and not force:
        return {"glid": str(glid), "seller_md": row["seller_md"], "persona": store.L(row["persona"], {}),
                "demand": store.L(row["demand"], {}), "raw": store.L(row["raw"], {}), "built_at": row["built_at"]}
    raw = load_raw(glid)
    md = seller_md(raw)
    demand = demand_pitch(raw)
    persona_failed = False
    try:
        persona, _ = llm.chat_json([{"role": "user", "content": PERSONA_PROMPT.format(
            raw=json.dumps({k: v for k, v in raw.items() if k not in ("demand",)}, ensure_ascii=False)[:6000])}],
            config.FIX_MODEL, max_tokens=3000, label="persona: ")
    except Exception as e:
        persona_failed = True
        persona = {"language": "hinglish-hindi-leaning", "formality": "respectful-informal", "pace": "medium",
                   "warmth": "high", "voice": {"gender": "female", "accent": "neutral"},
                   "opening": f"Namaste, {raw.get('company_name')} se baat ho rahi hai?", "playbook": {},
                   "avoid": [], "why": f"default persona ({str(e)[:80]})"}
    # Cache only a real persona. A fallback from a passing LLM error (an expired key, a timeout)
    # would otherwise stick to this seller for good; the next call retries instead.
    if not persona_failed:
        store.run("INSERT OR REPLACE INTO seller_ctx VALUES (?,?,?,?,?,?)",
                  (str(glid), md, store.J(persona), store.J(demand), store.J(raw), store.now()))
        assign_category(glid, raw)
    return {"glid": str(glid), "seller_md": md, "persona": persona, "demand": demand, "raw": raw,
            "built_at": store.now()}


def variables(glid):
    """What is passed to Sarvam when the call starts. Kept under ~1 KB."""
    c = build(glid)
    p = c["persona"]
    raw = c.get("raw") or {}
    # Level 3: what this seller told us on earlier calls
    facts = store.rows("SELECT fact FROM seller_fact WHERE glid=? AND removed=0 ORDER BY created DESC LIMIT 6",
                       (str(glid),))
    memory = ("\nRemembered from earlier calls: " + "; ".join(f["fact"] for f in facts)) if facts else ""
    # Level 2: the approved playbook for this seller's category
    cat = store.category_of(glid) or assign_category(glid, raw)
    cat_pb = (store.one("SELECT text FROM category_playbook WHERE category=?", (cat,)) or {}).get("text") if cat else None
    return {"glid": str(glid), "seller_name": raw.get("company_name") or f"Seller {glid}",
            "category": store.category_label(cat) if cat else "",
            "category_playbook": cat_pb or "",
            "seller_md": c["seller_md"] + memory,
            "persona": f"language={p.get('language')}; formality={p.get('formality')}; pace={p.get('pace')}; "
                       f"warmth={p.get('warmth')}; opening={p.get('opening')}",
            "playbook": "; ".join(f"{k}: {v}" for k, v in (p.get("playbook") or {}).items()),
            "avoid": "; ".join(p.get("avoid") or []),
            "hook": f"{c['demand']['bl_city_30d']} BuyLeads is mahine aapki category mein"}
