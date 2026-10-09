# Approach note — Problem 1: Quality Audit & Fix Loop

**Team:** _fill in_ · **Platform:** Sarvam Conversations · **Repo:** github.com/malayajsingh-bit/Voice-Hack-Token-

## The problem we are solving
Voice Bot call quality is checked by sampling a few calls into a spreadsheet. Most failures go unseen,
root causes are guessed, and fixes reach the prompt slowly and by hand.

## What we built
A loop in which **every call audits itself**, failures are grouped into **root causes**, the **fix is proposed as a
prompt diff**, **tested on a slice** against simulated sellers on Sarvam, and **promoted only if it wins**.
The same loop flags risky calls for human review and **sales-ready sellers for a pre-sales pitch during the call**.

    pre-call: seller.md + persona (per GLID) → Sarvam agent (states + API tools) → transcript
    → grade (Fatal / Non-Fatal, flags, sales-ready) → cluster root causes → rank by impact
    → propose fix (diff) → human approves → A/B on 10% + simulated sellers → promote → next call

## How the agent acts
- Opens personalised from pre-call variables; persona (language mix, pace, warmth, playbook) chosen from seller data.
- Adapts live through `set_persona` on rushed / frustrated / confused / interested / language switch / silence. Every switch is logged.
- Moves to a **pre-sales state** only via `flag_sales_ready`; there it asks for two minutes, quotes computed demand
  numbers from `get_demand_pitch` (buyers/month, business value, AOV, BuyLeads in the city, what the seller could make),
  and books a callback or sends the proposal.
- Guardrails are tools and states, not prompt hopes: two risk flags or a human request → transfer.

## Efficiency, measured by the pipeline itself
| metric | today | ours |
|---|---|---|
| calls audited | ~3% sample | 100% |
| agreement with human auditors | — | κ, Fatal precision/recall on the labelled subset |
| root-cause coverage | guessed | share of Fatal calls in a named cluster |
| time from issue to tested fix | days | minutes (dashboard timestamps) |
| failure-rate drop after a fix | unknown | A vs B on the cause, z-test, early stop on worse |
| sales-ready sellers found | not captured | per 100 calls, with precision on review |
| cost per audited call | auditor hours | ≈ $0.003 on transcripts |

## Technical choices
- Grader: Claude Sonnet 5 (schema-enforced). Fix proposer and cluster namer: Gemini 3.6 Flash — never the grader.
- Clustering: TF-IDF + k-means, k by silhouette, capped at 8; LLM names clusters from central examples.
- Storage: SQLite with a Postgres-compatible schema. Service: FastAPI. Dashboard: one page.
- Sarvam: multi-state agent, API tools pointing at our service, variables per call, simulated sellers for testing.

## What we would do next
Webhook ingest from Sarvam; embeddings instead of TF-IDF at scale; per-flag precision targets; a weekly
digest of promoted fixes with their measured drop.
