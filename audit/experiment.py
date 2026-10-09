"""Step 6: test an approved fix on a slice. A = current prompt, B = fixed prompt.
Goal = share of calls that are 'bad' on the cause (Fatal with this cause_id), or any
chosen goal disposition. Two-proportion z-test; early stop if B is clearly worse."""
import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "service"))
import store    # noqa: E402


def z_test(a_bad, a_n, b_bad, b_n):
    if not a_n or not b_n:
        return None
    p1, p2 = a_bad / a_n, b_bad / b_n
    p = (a_bad + b_bad) / (a_n + b_n)
    se = math.sqrt(p * (1 - p) * (1 / a_n + 1 / b_n)) or 1e-9
    z = (p1 - p2) / se
    # two-sided p from normal CDF
    pv = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))
    return {"a_rate": round(p1, 3), "b_rate": round(p2, 3), "z": round(z, 2), "p_value": round(pv, 4)}


def start(fix_id, share=0.1, window_min=120, goal="fatal_on_cause"):
    eid = store.nid()
    store.run("INSERT INTO experiment VALUES (?,?,?,?,?,0,0,0,0,NULL,NULL,?,NULL)",
              (eid, fix_id, share, window_min, goal, store.now()))
    store.run("UPDATE fix SET status='testing' WHERE id=?", (fix_id,))
    return eid


def assign(eid):
    """Which variant the next call gets. Deterministic share, random assignment."""
    import random
    e = store.one("SELECT * FROM experiment WHERE id=?", (eid,))
    return "B" if random.random() < float(e["share"]) else "A"


def record(eid, variant, bad):
    col_n, col_bad = ("b_calls", "b_bad") if variant == "B" else ("a_calls", "a_bad")
    store.run(f"UPDATE experiment SET {col_n}={col_n}+1, {col_bad}={col_bad}+? WHERE id=?", (1 if bad else 0, eid))


def status(eid, alpha=0.05, min_each=20):
    e = store.one("SELECT * FROM experiment WHERE id=?", (eid,))
    t = z_test(e["a_bad"], e["a_calls"], e["b_bad"], e["b_calls"])
    decision = None
    if t:
        if e["a_calls"] >= min_each and e["b_calls"] >= min_each:
            if t["p_value"] < alpha and t["b_rate"] < t["a_rate"]:
                decision = "promote"
            elif t["p_value"] < alpha and t["b_rate"] > t["a_rate"]:
                decision = "stop_worse"
        elif e["b_calls"] >= 10 and t["b_rate"] > t["a_rate"] + 0.25:
            decision = "stop_worse"            # early stop on a clearly worse variant
    return {**dict(e), "test": t, "decision": decision}


def finish(eid, decision):
    store.run("UPDATE experiment SET decision=?, finished=? WHERE id=?", (decision, store.now(), eid))
