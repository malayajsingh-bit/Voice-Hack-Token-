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


GROUP_FIX = """You write a short playbook for an IndiaMART sales voice bot, used ONLY with this group: {group}.
Failures seen in this group:
{examples}

Current playbook for this group (may be empty):
<<<
{current}
>>>

Write the complete new playbook: at most 4 short lines, each one a concrete instruction the bot follows
with this group (what to say or do, in plain English, passive voice, no quotes, no numbers it cannot
get from a tool). Keep any current line that still holds.

Return ONLY JSON: {{"playbook": "<the lines, separated by newlines>",
  "change_summary": "<one line: what changes>", "rationale": "<one line: why this removes the failures>"}}"""

SIT_HEAD = "## Situation playbooks"


def situation_block(prompt, label):
    """The text of one situation's block inside the prompt's Situation playbooks section."""
    if SIT_HEAD not in prompt:
        return ""
    sec = prompt.split(SIT_HEAD, 1)[1].split("\n## ", 1)[0]
    for para in sec.strip().split("\n\n"):
        if para.startswith(label + "."):
            return para[len(label) + 1:].strip()
    return ""


def with_situation_block(prompt, label, text):
    """Prompt with one situation block replaced (or added); everything else untouched."""
    block = f"{label}. " + " ".join(l.strip() for l in text.splitlines() if l.strip())
    if SIT_HEAD not in prompt:
        head, sep, tail = prompt.partition("\n## Guardrails")
        return f"{head.rstrip()}\n\n{SIT_HEAD}\n{block}\n{sep}{tail}"
    before, after = prompt.split(SIT_HEAD, 1)
    sec, sep, rest = after.partition("\n## ")
    paras = [p for p in sec.strip().split("\n\n") if p and not p.startswith(label + ".")] + [block]
    return f"{before}{SIT_HEAD}\n" + "\n\n".join(paras) + "\n" + (sep + rest if sep else "")


def propose_group(c, model):
    kind, key = (c["scope"] or ":").split(":", 1)
    label = store.SITUATIONS.get(key) if kind == "situation" else store.category_label(key)
    ids = store.L(c["examples"])
    ex = [r["reason"] for r in (store.one("SELECT reason FROM audit WHERE call_id=?", (i,)) for i in ids) if r]
    prompt = current_prompt()
    if kind == "situation":
        current = situation_block(prompt, label)
    else:
        current = (store.one("SELECT text FROM category_playbook WHERE category=?", (key,)) or {}).get("text", "")
    j, cost = llm.chat_json([{"role": "user", "content": GROUP_FIX.format(
        group=label, examples="\n".join(f"- {e}" for e in ex[:8]), current=current)}],
        model, max_tokens=3000, label="group fix: ")
    new_text = (j.get("playbook") or "").strip()
    if kind == "situation":
        new_prompt = with_situation_block(prompt, label, new_text)
        diff = "\n".join(difflib.unified_diff(prompt.splitlines(), new_prompt.splitlines(),
                                              "prompt (current)", "prompt (proposed)", lineterm=""))
        meta = {"kind": "prompt", "new_prompt": new_prompt}
    else:
        diff = "\n".join(difflib.unified_diff(current.splitlines(), new_text.splitlines(),
                                              f"{label} playbook (current)", f"{label} playbook (proposed)", lineterm=""))
        meta = {"kind": "category_playbook", "category": key, "playbook": new_text}
    meta.update({"summary": j.get("change_summary"), "rationale": j.get("rationale"), "level": 2,
                 "scope": c["scope"], "scope_label": label})
    fid = store.nid()
    store.run("INSERT INTO fix VALUES (?,?,?,?,?,?,?)",
              (fid, c["id"], diff, store.J(meta), "proposed", store.now(), None))
    return {"id": fid, "kind": meta["kind"], "diff": diff, "summary": j.get("change_summary"), "cost": cost}


def propose(cause_id, model=config.FIX_MODEL):
    c = store.one("SELECT * FROM root_cause WHERE id=?", (cause_id,))
    if not c:
        raise LookupError(cause_id)
    if (c.get("level") or 1) == 2:
        return propose_group(c, model)
    ids = store.L(c.get("examples"))
    ex = ([r for r in (store.one("SELECT reason FROM audit WHERE call_id=?", (i,)) for i in ids[:8]) if r]
          or store.rows("SELECT reason FROM audit WHERE cause_id=? LIMIT 8", (cause_id,)))
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
                        "test": j.get("test"), "kind": j.get("kind"), "new_prompt": j.get("new_prompt"),
                        "base_prompt": prompt}),
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
    if meta.get("kind") == "category_playbook":
        # applies to every seller in this category from the next call, via the per-call variable
        store.run("INSERT OR REPLACE INTO category_playbook VALUES (?,?,?,?)",
                  (meta["category"], meta["playbook"], fix_id, store.now()))
        store.run("UPDATE fix SET status='promoted', decided_at=? WHERE id=?", (store.now(), fix_id))
        meta["sarvam_pushed"] = store.now()          # nothing to push: it travels with each call
        store.run("UPDATE fix SET rationale=? WHERE id=?", (store.J(meta), fix_id))
        return {"category": meta["category"]}
    if meta.get("kind") != "prompt" or not meta.get("new_prompt"):
        raise ValueError("only prompt fixes can be promoted automatically")
    # Another fix may have gone live since this one was written: apply only this fix's own
    # change on top of the live prompt, so approving two fixes keeps both.
    live = current_prompt()
    new_prompt = rebase(meta.get("base_prompt") or live, meta["new_prompt"], live)
    v = (store.one("SELECT max(version) AS v FROM prompt_version")["v"] or 0) + 1
    store.run("UPDATE prompt_version SET active=0")
    store.run("INSERT INTO prompt_version VALUES (?,?,?,?,?,1)", (store.nid(), v, new_prompt, fix_id, store.now()))
    store.run("UPDATE fix SET status='promoted', decided_at=? WHERE id=?", (store.now(), fix_id))
    PROMPT_FILE.write_text(new_prompt, encoding="utf-8")
    return {"version": v}


def rebase(base, new, live):
    """Apply the base->new change to live. Each changed block is found verbatim in live (prompt
    lines are whole paragraphs, so they are unique); an insertion is anchored on the line before it."""
    if live == base:
        return new
    b, n, out = base.splitlines(), new.splitlines(), live.splitlines()
    for tag, i1, i2, j1, j2 in reversed(difflib.SequenceMatcher(None, b, n, autojunk=False).get_opcodes()):
        if tag == "equal":
            continue
        if i2 > i1:
            block = b[i1:i2]
            at = next((k for k in range(len(out) - len(block) + 1) if out[k:k + len(block)] == block), None)
            if at is None:
                raise ValueError(f"cannot apply fix: changed text no longer in the live prompt: {block[0][:80]}")
            out[at:at + len(block)] = n[j1:j2]
        else:
            anchor = b[i1 - 1] if i1 else None
            at = (out.index(anchor) + 1) if anchor in out else (0 if anchor is None else None)
            if at is None:
                raise ValueError("cannot apply fix: insertion point no longer in the live prompt")
            out[at:at] = n[j1:j2]
    return "\n".join(out) + ("\n" if new.endswith("\n") else "")
