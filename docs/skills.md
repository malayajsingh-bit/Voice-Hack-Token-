# skills.md — build journey and tools

_Fill in as we go. The jury reads this with the demo video._

## Team
- _name_ — audit pipeline (grading, clustering, fixes, experiments)
- _name_ — Sarvam agent (states, tools, persona, simulated sellers, telephony)
- _name_ — labels, dashboard content, demo script, this file

## Tools we used
- **Sarvam Conversations** — multi-state voice agent, API tools, variables, telephony, Bulbul/Saaras.
- **Claude Code** — pair-programmer for the service, audit pipeline, dashboard and docs. All code written
  during the two days; Claude Code drafted, we reviewed and ran every piece.
- **LLMs via gateway** — Claude Sonnet 5 as grader; Gemini 3.6 Flash for persona, cluster names and fix proposals.
- **Python** — FastAPI, scikit-learn (TF-IDF + k-means), SQLite.

## Day 1
- 10:30 — _keys, number, first agent that answers_
- 12:00 — _grading schema; agreement on labelled subset: κ = …_
- 15:30 — _mentor check-in: …_
- 18:00 — _top root causes: …_

## Day 2
- 10:00 — _fix proposed as diff; simulated sellers_
- 13:00 — _A/B run: cause X, A …% → B …%, p = …_
- 15:00 — _demo recorded_

## What worked
- _…_

## What did not, and what we changed
- Claude via the gateway spent its whole token budget on hidden reasoning for long transcripts and returned
  nothing; fixed by sending `reasoning: none` for Claude only (Gemini rejects the flag).
- _…_

## Numbers we are proud of
- Calls audited: 100% (vs ~3% sample) — on the 10 sample calls
- Agreement with human auditors: κ = _…_ on n = _…_
- Time from issue to tested fix: _…_ min
- Failure-rate drop on the top cause (replay test, samples): 100% → 40% Fatal on 5 calls, $0.08, 1 minute after the fix was proposed
