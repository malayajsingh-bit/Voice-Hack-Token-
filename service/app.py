#!/usr/bin/env python3
"""Context service + audit API + dashboard.   python3 service/app.py  -> :8800

Sarvam calls /tools/* during a call; /calls/ingest receives finished calls; the
dashboard drives approve → test → promote. Nothing here changes the live agent
except /fixes/{id}/promote, which is a human action on the dashboard."""
import json
import pathlib
import sys
import threading

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent / "audit"))
import config    # noqa: E402
import context   # noqa: E402
import sarvam    # noqa: E402
import store     # noqa: E402
import tools     # noqa: E402
import voices    # noqa: E402
import cluster as cl       # noqa: E402
import experiment as ex    # noqa: E402
import fix as fx           # noqa: E402
import grade as gr         # noqa: E402
import replay as rp        # noqa: E402
import sync as sy          # noqa: E402

app = FastAPI(title="Voice Bot Audit Loop")
TOOL_KEY = config._env("TOOL_KEY")


@app.middleware("http")
async def _guard(request, call_next):
    """Requests arriving through the public tunnel carry cf-connecting-ip. They may reach
    only the in-call tools and the post-call webhook, and only with the shared key.
    Everything else (dashboard, approve, promote) stays local."""
    if request.headers.get("cf-connecting-ip"):
        p = request.url.path
        if not (p.startswith("/tools/") or p == "/calls/ingest"):
            return JSONResponse({"error": "not available remotely"}, status_code=403)
        if TOOL_KEY and request.headers.get("x-tool-key") != TOOL_KEY:
            return JSONResponse({"error": "bad key"}, status_code=401)
    return await call_next(request)


async def _tool(req: Request, name: str, handler):
    """Runs one /tools/* handler and stores the request and the response, so a call's audit
    can see exactly what each tool returned."""
    b = await req.json()
    status, resp = "ok", None
    try:
        resp = handler(b)
        return resp
    except Exception as e:
        status, resp = "error", {"error": str(e)[:200]}
        raise
    finally:
        store.run("INSERT INTO tool_log VALUES (?,?,?,?,?,?,?,?)",
                  (store.nid(), str(b.get("call_id") or ""), str(b.get("glid") or ""), name,
                   store.J(b), store.J(resp), status, store.now()))


# ------------------------------------------------------------ pre-call -----
@app.get("/context/{glid}")
def get_context(glid: str, force: bool = False):
    return context.build(glid, force=force)


@app.get("/context/{glid}/variables")
def get_variables(glid: str):
    return context.variables(glid)


@app.post("/calls/start")
async def start_call(req: Request):
    b = await req.json()
    glid, phone = str(b["glid"]), b["phone"]
    v = context.variables(glid)
    exp = store.one("SELECT id FROM experiment WHERE decision IS NULL ORDER BY started DESC")
    variant = ex.assign(exp["id"]) if exp else "A"
    if variant == "B":
        v["prompt_override"] = store.L(store.one("SELECT rationale FROM fix WHERE id=(SELECT fix_id FROM experiment WHERE id=?)",
                                                 (exp["id"],))["rationale"], {}).get("new_prompt")
    try:
        r = sarvam.start_call(phone, v)
        cid = str(r.get("call_id") or r.get("id"))
    except Exception as e:
        cid = "dry-" + store.nid()
        r = {"dry_run": True, "error": str(e)[:200]}
    store.run("INSERT OR REPLACE INTO call VALUES (?,?,?,?,?,?,?,?,?,?)",
              (cid, glid, "live", store.now(), None, "", None, variant,
               store.J({"experiment": exp["id"] if exp else None, "variables": v}), store.now()))
    return {"call_id": cid, "variant": variant, "variables": v, "sarvam": r}


# ------------------------------------------------------------ in-call ------
@app.post("/tools/get_seller_context")
async def t_ctx(req: Request):
    def h(b):
        c = context.build(str(b.get("glid")))
        return {"seller_md": c["seller_md"], "persona": c["persona"], "hook": (c.get("demand") or {}).get("line", "")}
    return await _tool(req, "get_seller_context", h)


@app.post("/tools/set_persona")
async def t_persona(req: Request):
    return await _tool(req, "set_persona",
                       lambda b: tools.set_persona(b.get("glid"), b.get("call_id"), b.get("signal"), b.get("note", "")))


@app.post("/tools/flag_sales_ready")
async def t_sales(req: Request):
    return await _tool(req, "flag_sales_ready",
                       lambda b: tools.flag_sales_ready(b.get("glid"), b.get("call_id"),
                                                        b.get("reason", ""), b.get("confidence", 0.7)))


@app.post("/tools/get_demand_pitch")
async def t_pitch(req: Request):
    return await _tool(req, "get_demand_pitch", lambda b: tools.get_demand_pitch(b.get("glid")))


@app.post("/tools/book_callback")
async def t_cb(req: Request):
    return await _tool(req, "book_callback",
                       lambda b: tools.book_callback(b.get("glid"), b.get("call_id"), b.get("when", ""), b.get("note", "")))


@app.post("/tools/flag_risk")
async def t_risk(req: Request):
    return await _tool(req, "flag_risk",
                       lambda b: tools.flag_risk(b.get("glid"), b.get("call_id"), b.get("kind", ""), b.get("note", "")))


@app.post("/tools/assess_engagement")
async def t_engage(req: Request):
    def h(b):
        sig = b.get("signals") or []
        if isinstance(sig, str):
            sig = [x.strip() for x in sig.replace(";", ",").split(",") if x.strip()]
        ap = b.get("asked_price")
        return tools.assess_engagement(b.get("glid"), b.get("call_id"), sig,
                                       ap is True or str(ap).lower() in ("true", "yes", "1"))
    return await _tool(req, "assess_engagement", h)


@app.get("/route/{glid}")
def route(glid: str):
    """Which voice agent should call this seller, and why."""
    return voices.choose(glid)


@app.get("/tools/definitions")
def t_defs():
    return sarvam.tool_definitions(config.PUBLIC_URL)


# ------------------------------------------------------------ post-call ----
@app.post("/calls/ingest")
async def ingest(req: Request):
    """Sarvam post-call payload (or our own): {call_id, glid?, transcript, recording_url?, duration?}."""
    b = await req.json()
    cid = str(b.get("call_id") or b.get("id") or store.nid())
    t = b.get("transcript") or ""
    if isinstance(t, list):      # [{role, text}] -> "Bot: ...\nSeller: ..."
        t = "\n".join(f"{x.get('role', '?').title()}: {x.get('text', '')}" for x in t)
    old = store.one("SELECT * FROM call WHERE id=?", (cid,))
    store.run("INSERT OR REPLACE INTO call VALUES (?,?,?,?,?,?,?,?,?,?)",
              (cid, str(b.get("glid") or (old or {}).get("glid") or ""), (old or {}).get("source") or "live",
               (old or {}).get("started") or store.now(), b.get("duration"), t, b.get("recording_url"),
               (old or {}).get("variant") or "A",
               store.J({**store.L((old or {}).get("meta") or "{}", {}),
                        **{k: b[k] for k in ("voice", "agent_id", "agent_version", "sarvam_id") if b.get(k)}}),
               store.now()))

    def audit_one():
        j = gr.grade(cid, t)
        meta = store.L((old or {}).get("meta") or "{}", {})
        if meta.get("experiment"):
            bad = j.get("grade") == "Fatal"
            ex.record(meta["experiment"], (old or {}).get("variant") or "A", bad)
    if t:
        threading.Thread(target=audit_one, daemon=True).start()
    return {"ok": True, "call_id": cid, "queued": bool(t)}


@app.post("/calls/{cid}/human_grade")
async def human_grade(cid: str, req: Request):
    b = await req.json()
    store.run("INSERT OR IGNORE INTO audit (call_id) VALUES (?)", (cid,))
    store.run("UPDATE audit SET human_grade=? WHERE call_id=?", (b.get("grade"), cid))
    return {"ok": True}


# ------------------------------------------------------------ audit API ----
@app.get("/audit/summary")
def audit_summary():
    tot = store.one("SELECT count(*) AS n FROM call WHERE transcript<>''")["n"]
    g = store.one("SELECT count(*) AS n, coalesce(sum(grade='Fatal'),0) AS fatal, coalesce(sum(sales_ready),0) AS sr, "
                  "coalesce(sum(cost),0) AS cost FROM audit WHERE grade IS NOT NULL")
    sw = store.one("SELECT count(*) AS n FROM switch_log")["n"]
    q = {r["kind"]: r["n"] for r in store.rows("SELECT kind, count(*) AS n FROM queue WHERE handled_by IS NULL GROUP BY kind")}
    fixes = store.one("SELECT count(*) AS n, coalesce(sum(status='promoted'),0) AS promoted FROM fix")
    ttf = store.rows("""SELECT f.created AS proposed, f.decided_at AS decided, rc.created AS found FROM fix f
                        JOIN root_cause rc ON rc.id=f.cause_id WHERE f.status IN ('promoted','testing')""")
    return {"calls": tot, "audited": g["n"], "audited_pct": round(g["n"] / tot * 100, 1) if tot else 0,
            "fatal": g["fatal"], "fatal_pct": round(g["fatal"] / g["n"] * 100, 1) if g["n"] else 0,
            "sales_ready": g["sr"], "cost_usd": round(g["cost"], 3),
            "cost_per_call_usd": round(g["cost"] / g["n"], 4) if g["n"] else 0,
            "agreement": gr.agreement(), "switches": sw, "queues": q,
            "fixes": fixes["n"], "promoted": fixes["promoted"], "time_to_fix": ttf}


@app.get("/calls")
def calls(limit: int = 200):
    return store.rows("""SELECT c.id, c.glid, c.source, c.variant, c.created, a.grade, a.reason, a.flags, a.sales_ready,
                         a.human_grade, a.cause_id FROM call c LEFT JOIN audit a ON a.call_id=c.id
                         ORDER BY c.created DESC LIMIT ?""", (limit,))


@app.get("/calls/{cid}")
def call(cid: str):
    c = store.one("SELECT * FROM call WHERE id=?", (cid,))
    if not c:
        raise HTTPException(404)
    c["audit"] = store.one("SELECT * FROM audit WHERE call_id=?", (cid,))
    c["switches"] = store.rows("SELECT * FROM switch_log WHERE call_id=? ORDER BY t", (cid,))
    c["tools"] = [{"tool": t["tool"], "at": t["created"], "status": t["status"],
                   "request": store.L(t["request"], {}), "response": store.L(t["response"], {})}
                  for t in store.tool_calls_for(cid)]
    return c


@app.post("/audit/cluster")
def do_cluster():
    return {"level1": cl.cluster(), "level2": cl.cluster_groups()}


@app.get("/causes")
def causes():
    out = cl.ranked()
    for c in out:
        c["fixes"] = store.rows("SELECT id, status, created FROM fix WHERE cause_id=? ORDER BY created DESC", (c["id"],))
    return out


@app.get("/causes/{cid}")
def cause(cid: str):
    c = store.one("SELECT * FROM root_cause WHERE id=?", (cid,))
    if not c:
        raise HTTPException(404)
    c["calls"] = store.rows("SELECT call_id, grade, reason, flags FROM audit WHERE cause_id=?", (cid,))
    c["fixes"] = store.rows("SELECT * FROM fix WHERE cause_id=? ORDER BY created DESC", (cid,))
    return c


@app.post("/causes/{cid}/propose")
def propose(cid: str):
    return fx.propose(cid)


@app.get("/fixes")
def fixes():
    return store.rows("SELECT f.*, rc.name AS cause FROM fix f LEFT JOIN root_cause rc ON rc.id=f.cause_id ORDER BY f.created DESC")


STAGES = ["Proposed", "Tested", "Approved", "Live"]


def _card(f):
    """Everything one approval card shows, already reduced to what a person reads."""
    m = store.L(f["rationale"], {})
    rc = store.one("SELECT * FROM root_cause WHERE id=?", (f["cause_id"],)) or {}
    first = (store.L(rc.get("examples")) or [None])[0]
    ex_row = (store.one("SELECT reason FROM audit WHERE call_id=? AND reason LIKE '%—%'", (first,))
              or store.one("SELECT reason FROM audit WHERE cause_id=? AND reason LIKE '%—%' LIMIT 1", (f["cause_id"],)))
    quote = ""
    if ex_row and '"' in ex_row["reason"]:
        quote = ex_row["reason"].split('"', 1)[1].rsplit('"', 1)[0][:180]
    lines = [l for l in (f["prompt_diff"] or "").splitlines()
             if (l.startswith("+") or l.startswith("-")) and not l.startswith(("+++", "---"))]
    rp_ = m.get("replay") or {}
    stage = {"proposed": 1 if rp_ else 0, "approved": 2, "testing": 2, "promoted": 3}.get(f["status"], 0)
    if f["status"] == "promoted" and not m.get("sarvam_pushed"):
        stage = 2.5
    return {"id": f["id"], "status": f["status"], "stage": stage,
            "level": (rc.get("level") or 1), "scope_label": m.get("scope_label") or "",
            "scope_kind": (rc.get("scope") or "").split(":")[0],
            "problem": rc.get("name") or "Unnamed issue", "calls": rc.get("count") or 0,
            "fatal": rc.get("fatal_count") or 0,
            "what_changes": (m.get("summary") or "").split("\n")[0].split(". ")[0].rstrip(".") + ".",
            "kind": m.get("kind"), "removed": [l[1:].strip() for l in lines if l.startswith("-")],
            "added": [l[1:].strip() for l in lines if l.startswith("+")],
            "why": (m.get("rationale") or "")[:300], "quote": quote,
            "before": rp_.get("before_rate"), "after": rp_.get("after_rate"), "tested_on": rp_.get("n"),
            "sarvam_pushed": bool(m.get("sarvam_pushed")), "created": f["created"]}


def _scope_label(scope):
    kind, _, key = (scope or "").partition(":")
    if kind == "situation":
        return store.SITUATIONS.get(key, key)
    if kind == "category":
        return store.category_label(key)
    return ""


@app.get("/approvals")
def approvals():
    fx_rows = store.rows("SELECT * FROM fix ORDER BY created DESC")
    cards = [_card(f) for f in fx_rows if f["status"] != "rejected"]
    with_fix = {f["cause_id"] for f in fx_rows if f["status"] != "rejected"}
    out = {"stages": STAGES}
    for lvl in (1, 2):
        cs = [c for c in cards if c["level"] == lvl]
        out[f"level{lvl}"] = {
            "waiting": [c for c in cs if c["stage"] < 3], "live": [c for c in cs if c["stage"] >= 3],
            "no_fix_yet": [{"id": c["id"], "problem": c["name"], "calls": c["count"], "fatal": c["fatal_count"],
                            "scope_label": _scope_label(c.get("scope")), "scope_kind": (c.get("scope") or "").split(":")[0]}
                           for c in cl.ranked(lvl) if c["id"] not in with_fix]}
    out["level3"] = sellers_memory()
    return out


@app.get("/sellers/memory")
def sellers_memory():
    facts = store.rows("SELECT * FROM seller_fact WHERE removed=0 ORDER BY created DESC")
    by = {}
    for f in facts:
        by.setdefault(f["glid"], []).append(f)
    out = []
    for glid, fs in by.items():
        ctx = store.one("SELECT raw FROM seller_ctx WHERE glid=?", (glid,))
        name = store.L(ctx["raw"], {}).get("company_name") if ctx else None
        last = store.one("SELECT max(created) AS t FROM call WHERE glid=?", (glid,))["t"]
        out.append({"glid": glid, "name": name or f"Seller {glid}", "last_call": last,
                    "facts": [{"id": f["id"], "kind": f["kind"], "fact": f["fact"], "quote": f["quote"],
                               "check_data": bool(f["check_data"]), "call_id": f["call_id"]} for f in fs]})
    return out


@app.post("/sellers/facts/{fid}/remove")
def remove_fact(fid: str):
    store.run("UPDATE seller_fact SET removed=1 WHERE id=?", (fid,))
    return {"ok": True}


@app.post("/fixes/{fid}/mark_pushed")
def mark_pushed(fid: str):
    """Recorded once the approved prompt is on the Sarvam agent (pushed via MCP today)."""
    f = store.one("SELECT rationale FROM fix WHERE id=?", (fid,))
    m = store.L(f["rationale"], {})
    m["sarvam_pushed"] = store.now()
    store.run("UPDATE fix SET rationale=? WHERE id=?", (store.J(m), fid))
    return {"ok": True}


@app.post("/fixes/{fid}/approve")
async def approve(fid: str, req: Request):
    b = await req.json() if await req.body() else {}
    fx.approve(fid)
    eid = ex.start(fid, float(b.get("share", 0.1)), int(b.get("window_min", 120)))
    return {"ok": True, "experiment": eid}


@app.post("/fixes/{fid}/replay")
def replay_fix(fid: str, limit: int = 10):
    return rp.run(fid, limit)


@app.post("/calls/sync")
def calls_sync(hours: int = 24):
    return {"synced": sy.sync(hours)}


@app.post("/fixes/{fid}/reject")
def reject(fid: str):
    fx.reject(fid)
    return {"ok": True}


@app.post("/fixes/{fid}/promote")
def promote(fid: str):
    r = fx.promote(fid)
    # Sarvam has no documented REST call for editing an agent's prompt; the new version is
    # pushed with the MCP (configure_agent + commit), then /fixes/{id}/mark_pushed records it.
    return {"ok": True, **r, "sarvam": "waiting to be pushed"}


@app.get("/experiments")
def experiments():
    return [ex.status(e["id"]) for e in store.rows("SELECT id FROM experiment ORDER BY started DESC")]


@app.post("/experiments/{eid}/record")
async def exp_record(eid: str, req: Request):
    """Manual/simulated recording: {variant:'A'|'B', bad:true|false}."""
    b = await req.json()
    ex.record(eid, b.get("variant", "A"), bool(b.get("bad")))
    return ex.status(eid)


@app.post("/experiments/{eid}/finish")
async def exp_finish(eid: str, req: Request):
    b = await req.json()
    ex.finish(eid, b.get("decision"))
    return {"ok": True}


@app.get("/queue/{kind}")
def queue(kind: str):
    return store.rows("SELECT * FROM queue WHERE kind=? AND handled_by IS NULL ORDER BY created DESC", (kind,))


@app.post("/queue/{qid}/handle")
def handle(qid: str):
    store.run("UPDATE queue SET handled_by='dashboard' WHERE id=?", (qid,))
    return {"ok": True}


@app.get("/prompt")
def prompt():
    return {"prompt": fx.current_prompt(),
            "versions": store.rows("SELECT id, version, fix_id, created, active FROM prompt_version ORDER BY version DESC")}


# ------------------------------------------------------------ dashboard ----
@app.get("/")
def index():
    return FileResponse(HERE.parent / "dashboard" / "index.html")


if __name__ == "__main__":
    print("\n  Voice Bot Audit Loop -> http://localhost:8800\n")
    uvicorn.run(app, host="0.0.0.0", port=8800, log_level="warning")
