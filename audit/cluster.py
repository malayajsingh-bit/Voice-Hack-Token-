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
        c.execute("DELETE FROM root_cause")
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
        store.run("INSERT INTO root_cause VALUES (?,?,?,?,?,?,?,?,?)",
                  (cid, nm.get("name"), nm.get("description"), len(idx), fatal, round(severity, 2),
                   round(len(idx) * severity, 1), store.J([rows[i]["call_id"] for _, i in d[:8]]), store.now()))
        for i in idx:
            store.run("UPDATE audit SET cause_id=? WHERE call_id=?", (cid, rows[i]["call_id"]))
        out.append({"id": cid, "name": nm.get("name"), "count": len(idx), "fatal": fatal,
                    "impact": round(len(idx) * severity, 1)})
    out.sort(key=lambda x: -x["impact"])
    return out


def ranked():
    return store.rows("SELECT * FROM root_cause ORDER BY impact DESC")
