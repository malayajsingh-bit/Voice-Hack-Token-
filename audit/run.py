#!/usr/bin/env python3
"""Batch audit over a folder of calls.

    python3 audit/run.py import  data/calls/          # .json or .txt per call -> call table
    python3 audit/run.py grade   [--limit N]            # grade ungraded calls
    python3 audit/run.py cluster                        # root causes + ranking
    python3 audit/run.py fix <cause_id>                 # propose a fix
    python3 audit/run.py summary

Call file formats: {"id","glid","transcript","human_grade"?,"outcome"?} or plain .txt (id = filename)."""
import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent / "service"))
import cluster as cl   # noqa: E402
import fix as fx       # noqa: E402
import grade as gr     # noqa: E402
import store           # noqa: E402


def import_calls(folder):
    n = 0
    for f in sorted(pathlib.Path(folder).glob("*")):
        if f.suffix == ".json":
            j = json.loads(f.read_text(encoding="utf-8"))
            cid, t = str(j.get("id") or f.stem), j.get("transcript", "")
            glid, human = j.get("glid"), j.get("human_grade")
            meta = {k: v for k, v in j.items() if k not in ("transcript",)}
        elif f.suffix == ".txt":
            cid, t, glid, human, meta = f.stem, f.read_text(encoding="utf-8"), None, None, {}
        else:
            continue
        store.run("INSERT OR REPLACE INTO call VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (cid, glid, "dataset", meta.get("started"), meta.get("duration"), t, meta.get("recording_url"),
                   meta.get("variant", "A"), store.J(meta), store.now()))
        if human:
            store.run("INSERT OR IGNORE INTO audit (call_id, human_grade) VALUES (?,?)", (cid, human))
            store.run("UPDATE audit SET human_grade=? WHERE call_id=?", (human, cid))
        n += 1
    print(f"imported {n} calls")


def grade_all(limit=None):
    rows = store.rows("""SELECT c.id, c.transcript FROM call c LEFT JOIN audit a ON a.call_id=c.id
                         WHERE a.grade IS NULL AND c.transcript <> ''""")
    if limit:
        rows = rows[:limit]
    cost = 0.0
    for i, r in enumerate(rows, 1):
        j = gr.grade(r["id"], r["transcript"])
        cost += j.get("cost", 0)
        print(f"[{i}/{len(rows)}] {r['id']}: {j.get('grade')} — {j.get('reason', '')[:80]}")
    print(f"graded {len(rows)}  ${cost:.3f}")
    print("agreement:", gr.agreement())


def summary():
    tot = store.one("SELECT count(*) AS n FROM call")["n"]
    g = store.one("SELECT count(*) AS n, sum(grade='Fatal') AS fatal, sum(sales_ready) AS sr FROM audit WHERE grade IS NOT NULL")
    print(f"calls {tot} · audited {g['n']} ({(g['n']/tot*100 if tot else 0):.0f}%) · fatal {g['fatal']} · sales-ready {g['sr']}")
    print("agreement:", gr.agreement())
    for c in cl.ranked():
        print(f"  {c['id']}  impact {c['impact']:>6}  n={c['count']:<3} fatal={c['fatal_count']:<3} {c['name']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["import", "grade", "cluster", "fix", "summary"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    if a.cmd == "import":
        import_calls(a.arg or "data/calls")
    elif a.cmd == "grade":
        grade_all(a.limit)
    elif a.cmd == "cluster":
        for c in cl.cluster():
            print(c)
    elif a.cmd == "fix":
        r = fx.propose(a.arg)
        print(json.dumps({k: v for k, v in r.items() if k != "diff"}, indent=1, ensure_ascii=False))
        print(r["diff"])
    else:
        summary()
