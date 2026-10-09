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
import cluster as cl       # noqa: E402
import experiment as ex    # noqa: E402
import fix as fx           # noqa: E402
import grade as gr         # noqa: E402

app = FastAPI(title="Voice Bot Audit Loop")


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
    b = await req.json()
    c = context.build(str(b.get("glid")))
    return {"seller_md": c["seller_md"], "persona": c["persona"], "hook": c["demand"]["line"]}


@app.post("/tools/set_persona")
async def t_persona(req: Request):
    b = await req.json()
    return tools.set_persona(b.get("glid"), b.get("call_id"), b.get("signal"), b.get("note", ""))


@app.post("/tools/flag_sales_ready")
async def t_sales(req: Request):
    b = await req.json()
    return tools.flag_sales_ready(b.get("glid"), b.get("call_id"), b.get("reason", ""), b.get("confidence", 0.7))


@app.post("/tools/get_demand_pitch")
async def t_pitch(req: Request):
    b = await req.json()
    return tools.get_demand_pitch(b.get("glid"))


@app.post("/tools/book_callback")
async def t_cb(req: Request):
    b = await req.json()
    return tools.book_callback(b.get("glid"), b.get("call_id"), b.get("when", ""), b.get("note", ""))


@app.post("/tools/flag_risk")
async def t_risk(req: Request):
    b = await req.json()
    return tools.flag_risk(b.get("glid"), b.get("call_id"), b.get("kind", ""), b.get("note", ""))


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
               (old or {}).get("variant") or "A", (old or {}).get("meta") or "{}", store.now()))

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
    return c


@app.post("/audit/cluster")
def do_cluster():
    return cl.cluster()


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


@app.post("/fixes/{fid}/approve")
async def approve(fid: str, req: Request):
    b = await req.json() if await req.body() else {}
    fx.approve(fid)
    eid = ex.start(fid, float(b.get("share", 0.1)), int(b.get("window_min", 120)))
    return {"ok": True, "experiment": eid}


@app.post("/fixes/{fid}/reject")
def reject(fid: str):
    fx.reject(fid)
    return {"ok": True}


@app.post("/fixes/{fid}/promote")
def promote(fid: str):
    r = fx.promote(fid)
    pushed = None
    if config.SARVAM_API_KEY and config.SARVAM_AGENT_ID:
        try:
            pushed = sarvam.update_agent(config.SARVAM_AGENT_ID, prompt=fx.current_prompt())
        except Exception as e:
            pushed = {"error": str(e)[:200]}
    return {"ok": True, **r, "sarvam": pushed}


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
