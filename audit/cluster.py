"""Steps 3-4: group failure reasons into root causes and rank them.

TF-IDF + k-means over `reason` (+ flags), k picked by silhouette, capped at 8; an LLM
names each cluster from its most central examples so causes read like sentences,
not cluster ids. impact = count × severity (Fatal 3, Non-Fatal 1, +1 with frustration)."""
import pathlib
import sys

import numpy as np
from sklearn.cluster import KMeans
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import silhouette_score

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "service"))
import config   # noqa: E402
import llm      # noqa: E402
import store    # noqa: E402

NAME_PROMPT = """These are failure reasons from audited Voice Bot sales calls that were grouped together.
Name the ROOT CAUSE in the bot's behaviour (not the symptom), as one short sentence a prompt
engineer could act on, and describe it in two lines.

Examples (most central first):
{examples}

Return ONLY JSON: {{"name": "<= 12 words", "description": "<two lines>"}}"""


def cluster(max_k=8):
    rows = store.rows("SELECT call_id, grade, reason, flags FROM audit WHERE reason IS NOT NULL AND grade='Fatal'")
    if len(rows) < 4:
        rows = store.rows("SELECT call_id, grade, reason, flags FROM audit WHERE reason IS NOT NULL")
    if len(rows) < 4:
        return []
    docs = [f"{r['reason']} {' '.join(store.L(r['flags']))}" for r in rows]
    X = TfidfVectorizer(ngram_range=(1, 2), min_df=1, sublinear_tf=True).fit_transform(docs)
    best, best_k = None, 1
    for k in range(2, min(max_k, len(rows) - 1) + 1):
        km = KMeans(n_clusters=k, n_init=10, random_state=7).fit(X)
        if len(set(km.labels_)) < 2:
            continue
        s = silhouette_score(X, km.labels_)
        if best is None or s > best[0]:
            best, best_k = (s, km), k
    km = best[1] if best else KMeans(n_clusters=1, n_init=1, random_state=7).fit(X)
    labels = km.labels_
    with store.db() as c:
        c.execute("DELETE FROM root_cause WHERE coalesce(level,1)=1")
    out = []
    for k in sorted(set(labels)):
        idx = [i for i, l in enumerate(labels) if l == k]
        centre = km.cluster_centers_[k]
        d = [(float(np.linalg.norm(X[i].toarray() - centre)), i) for i in idx]
        d.sort()
        ex = [rows[i] for _, i in d[:6]]
        try:
            nm, _ = llm.chat_json([{"role": "user", "content": NAME_PROMPT.format(
                examples="\n".join(f"- {e['reason']}" for e in ex))}], config.NAME_MODEL, max_tokens=2000, label="name: ")
        except Exception:
            nm = {"name": ex[0]["reason"][:60], "description": ""}
        fatal = sum(1 for i in idx if rows[i]["grade"] == "Fatal")
        frus = sum(1 for i in idx if "frustration" in store.L(rows[i]["flags"]))
        severity = (3 * fatal + 1 * (len(idx) - fatal)) / len(idx) + (1 if frus > len(idx) / 3 else 0)
        cid = store.nid()
        store.run("""INSERT INTO root_cause (id, name, description, count, fatal_count, severity, impact, examples,
                     created, level, scope) VALUES (?,?,?,?,?,?,?,?,?,1,NULL)""",
                  (cid, nm.get("name"), nm.get("description"), len(idx), fatal, round(severity, 2),
                   round(len(idx) * severity, 1), store.J([rows[i]["call_id"] for _, i in d[:8]]), store.now()))
        for i in idx:
            store.run("UPDATE audit SET cause_id=? WHERE call_id=?", (cid, rows[i]["call_id"]))
        out.append({"id": cid, "name": nm.get("name"), "count": len(idx), "fatal": fatal,
                    "impact": round(len(idx) * severity, 1)})
    out.sort(key=lambda x: -x["impact"])
    retire_orphan_fixes()
    return out


def ranked(level=None):
    if level:
        return store.rows("SELECT * FROM root_cause WHERE coalesce(level,1)=? ORDER BY impact DESC", (level,))
    return store.rows("SELECT * FROM root_cause ORDER BY impact DESC")


GROUP_PROMPT = """These failures all happened with IndiaMART sellers in the same group: {group}.
Name the ONE behaviour of the voice bot, specific to this group, that a playbook rule could fix.
Short sentence a sales trainer would write. Then two lines of description.

Failures:
{examples}

Return ONLY JSON: {{"name": "<= 14 words", "description": "<two lines>"}}"""


def _category_of_call(call_id):
    c = store.one("SELECT glid FROM call WHERE id=?", (call_id,)) or {}
    return store.category_of(c.get("glid")) if c.get("glid") else None


def cluster_groups(min_calls=2):
    """Level 2: one root cause per disposition and per seller category that has repeated failures.
    No k-means here: the group IS the cluster; the LLM names what goes wrong within it."""
    rows = store.rows("SELECT call_id, grade, reason, flags FROM audit WHERE grade='Fatal' AND reason IS NOT NULL")
    groups = {}
    for r in rows:
        for s in store.rows("SELECT situation FROM call_situation WHERE call_id=?", (r["call_id"],)):
            groups.setdefault(("situation", s["situation"]), []).append(r)
        cat = _category_of_call(r["call_id"])
        if cat:
            groups.setdefault(("category", cat), []).append(r)
    with store.db() as c:
        c.execute("DELETE FROM root_cause WHERE level=2")
    out = []
    for (kind, key), rs in groups.items():
        if len(rs) < min_calls:
            continue
        label = store.SITUATIONS.get(key) if kind == "situation" else store.category_label(key)
        try:
            nm, _ = llm.chat_json([{"role": "user", "content": GROUP_PROMPT.format(
                group=label, examples="\n".join(f"- {x['reason']}" for x in rs[:8]))}],
                config.NAME_MODEL, max_tokens=2000, label="group: ")
        except Exception:
            nm = {"name": rs[0]["reason"][:60], "description": ""}
        cid = store.nid()
        n = len(rs)
        store.run("""INSERT INTO root_cause (id, name, description, count, fatal_count, severity, impact, examples,
                     created, level, scope) VALUES (?,?,?,?,?,?,?,?,?,2,?)""",
                  (cid, nm.get("name"), nm.get("description"), n, n, 3.0, 3.0 * n,
                   store.J([x["call_id"] for x in rs[:8]]), store.now(), f"{kind}:{key}"))
        out.append({"id": cid, "scope": f"{kind}:{key}", "label": label, "name": nm.get("name"), "count": n})
    retire_orphan_fixes()
    return out


def retire_orphan_fixes():
    """Fixes whose problem disappeared in a regroup can no longer be judged against it."""
    store.run("""UPDATE fix SET status='rejected', decided_at=? WHERE status IN ('proposed','approved','testing')
                 AND cause_id NOT IN (SELECT id FROM root_cause)""", (store.now(),))
