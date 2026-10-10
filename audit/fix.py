"""Step 5: propose the fix for a root cause as a diff against the current prompt.
The model that proposes fixes is never the grader (config.FIX_MODEL vs GRADE_MODEL)."""
import difflib
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "service"))
import config   # noqa: E402
import llm      # noqa: E402
import store    # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROMPT_FILE = ROOT / "agent" / "sarvam_prompt.md"   # the prompt deployed on Sarvam

FIX_PROMPT = """You maintain the system prompt of an outbound sales Voice Bot (Hinglish, Indian MSME sellers).
An audit found this ROOT CAUSE of failed calls:

NAME: {name}
DESCRIPTION: {description}
EXAMPLE FAILURE REASONS:
{examples}

CURRENT PROMPT:
<<<
{prompt}
>>>

Propose the SMALLEST change to the prompt that removes this cause without weakening anything
else. Prefer adding or editing one rule or one state instruction over rewriting sections.
If the right fix is not in the prompt (needs a tool, a state transition, or data), say so.

Return ONLY JSON:
{{"kind": "prompt|tool|state|data",
  "new_prompt": "<the FULL prompt after your change, or null if kind != prompt>",
  "change_summary": "<two lines>",
  "rationale": "<why this removes the cause>",
  "test": "<how to reproduce the failure with a simulated seller, two lines>"}}"""


def current_prompt():
    row = store.one("SELECT prompt FROM prompt_version WHERE active=1 ORDER BY version DESC")
    if row:
        return row["prompt"]
    return PROMPT_FILE.read_text(encoding="utf-8") if PROMPT_FILE.exists() else ""


def propose(cause_id, model=config.FIX_MODEL):
    c = store.one("SELECT * FROM root_cause WHERE id=?", (cause_id,))
    if not c:
        raise LookupError(cause_id)
    ex = store.rows("SELECT reason FROM audit WHERE cause_id=? LIMIT 8", (cause_id,))
    prompt = current_prompt()
    j, cost = llm.chat_json([{"role": "user", "content": FIX_PROMPT.format(
        name=c["name"], description=c["description"], examples="\n".join(f"- {e['reason']}" for e in ex),
        prompt=prompt)}], model, max_tokens=6000, label="fix: ")
    diff = ""
    if j.get("kind") == "prompt" and j.get("new_prompt"):
        diff = "\n".join(difflib.unified_diff(prompt.splitlines(), j["new_prompt"].splitlines(),
                                              "prompt (current)", "prompt (proposed)", lineterm=""))
    fid = store.nid()
    store.run("INSERT INTO fix VALUES (?,?,?,?,?,?,?)",
              (fid, cause_id, diff or f"[{j.get('kind')}] {j.get('change_summary')}",
               store.J({"summary": j.get("change_summary"), "rationale": j.get("rationale"),
                        "test": j.get("test"), "kind": j.get("kind"), "new_prompt": j.get("new_prompt")}),
               "proposed", store.now(), None))
    return {"id": fid, "kind": j.get("kind"), "diff": diff, "summary": j.get("change_summary"),
            "rationale": j.get("rationale"), "test": j.get("test"), "cost": cost}


def approve(fix_id):
    store.run("UPDATE fix SET status='approved', decided_at=? WHERE id=?", (store.now(), fix_id))


def reject(fix_id):
    store.run("UPDATE fix SET status='rejected', decided_at=? WHERE id=?", (store.now(), fix_id))


def promote(fix_id):
    """Make the fix's prompt the active version. The dashboard then pushes it to Sarvam."""
    f = store.one("SELECT * FROM fix WHERE id=?", (fix_id,))
    meta = store.L(f["rationale"], {})
    if meta.get("kind") != "prompt" or not meta.get("new_prompt"):
        raise ValueError("only prompt fixes can be promoted automatically")
    v = (store.one("SELECT max(version) AS v FROM prompt_version")["v"] or 0) + 1
    store.run("UPDATE prompt_version SET active=0")
    store.run("INSERT INTO prompt_version VALUES (?,?,?,?,?,1)", (store.nid(), v, meta["new_prompt"], fix_id, store.now()))
    store.run("UPDATE fix SET status='promoted', decided_at=? WHERE id=?", (store.now(), fix_id))
    PROMPT_FILE.write_text(meta["new_prompt"], encoding="utf-8")
    return {"version": v}
