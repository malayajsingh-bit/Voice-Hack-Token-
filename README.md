# Voice Bot Audit Loop

Voice AI Hackathon 2.0 · 9–10 October 2026 · Problem 1: Quality Audit & Fix Loop

Every Voice Bot call grades itself. Failures are grouped into root causes, the fix is
proposed as a prompt diff, tested on a Sarvam bot against simulated sellers, and promoted
only when it wins. During the call the same bot adapts its persona to the seller and, when
the seller is engaged, moves into a pre-sales state and pitches the live demand in his
category.

Design document (architecture, data flow, state machine, metrics, two-day plan):
https://claude.ai/artifact/5pLmJ1sCYfX8EjeRGRcPH3

## The loop

    pre-call                      call                              post-call
    seller.md + persona  ──►  Sarvam agent  ──►  transcript  ──►  grade → flags → root causes
    (per GLID, cached)        states + tools                        → fix (prompt diff)
                                                                     → approve → test on slice
                                                                     → promote → next call

## Parts

| folder | what |
|---|---|
| `service/` | FastAPI context service: `/context/{glid}`, tool endpoints, `/calls/ingest`, audit and experiment APIs |
| `audit/` | grading, flags, clustering, ranking, fix proposals, experiment stats |
| `agent/` | `agent.yaml` (states, tools, prompt template), simulated sellers, deploy script for Sarvam |
| `dashboard/` | single page: audit → issue → fix → test, risk queue, pre-sales queue |
| `data/` | local only, git-ignored: call dataset, transcripts, audits, SQLite |
| `docs/` | approach note, skills.md, demo script |

## In-call tools (Sarvam API tools → our service)

- `get_seller_context` — seller.md fallback and refresh
- `set_persona` — mid-call adaptation on frustration / rush / confusion / language switch
- `flag_sales_ready` — moves the call into the pre-sales state
- `get_demand_pitch` — buyers/month, business value, AOV, BuyLeads in his city, what he could make
- `book_callback`, `flag_risk`, transfer (built-in)

## Metrics we report

Calls audited (sample → 100%), agreement with human auditors (κ, Fatal recall), root-cause
coverage, time from issue to tested fix, failure-rate drop per fix, persona-switch accuracy,
sales-ready yield, cost per audited call.

## Rules we keep

- No prompt reaches the live agent without a human approval on the dashboard.
- Fixes are tested on a slice before promotion; the grader is never the model that wrote the fix.
- Only the call variables leave our side; transcripts and recordings are audited here.
- All code written during the two days. Claude Code is used as a tool and recorded in `skills.md`.

## Run

    pip install -r requirements.txt
    cp .env.example .env            # fill LLM_BASE_URL, LLM_API_KEY, SARVAM_API_KEY, PUBLIC_URL
    python3 audit/run.py import audit/samples     # or data/calls/ with the hackathon dataset
    python3 audit/run.py grade                    # grade every call; prints agreement with human labels
    python3 audit/run.py cluster                  # root causes, ranked
    python3 service/app.py                        # http://localhost:8800 — dashboard + tools + ingest
    python3 agent/deploy.py --dry                 # agent payload for Sarvam (set keys to deploy)

Expose the service for Sarvam tools: `cloudflared tunnel --url http://localhost:8800` and put the
URL in `PUBLIC_URL`. On the sample calls: 10/10 audited, κ = 1.0 vs human labels, top cause named,
fix proposed as a one-line prompt diff, $0.03 total.
