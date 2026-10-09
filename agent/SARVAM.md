# The live Sarvam agent

- **Agent**: `IndiaMART Seller Growth Call` · app id `IndiaMART-S-31ca33eb-e1ce` · committed version 1
- **Voice**: Sarika (conversational Hindi female, Sarvam TTS v4) · languages Hindi + English
- **Workspace constraint**: one state only, so the five phases (open, discover, objection, pre-sales, close)
  are sections of one prompt; pre-sales is gated by the `flag_sales_ready` tool and the `sales_stage` variable.
- **Per-call variables** (from `/context/{glid}/variables`): glid, seller_name, seller_md, persona, playbook, hook
- **Written by tools mid-call**: live_instruction (set_persona), sales_stage (flag_sales_ready)
- **Extracted after the call**: call_outcome (enum), sales_ready, main_objection, callback_time
- **API tools** → `PUBLIC_URL/tools/*` with header `x-tool-key`: set_persona, flag_sales_ready, get_demand_pitch,
  book_callback, flag_risk. The service refuses tunnel traffic to anything except `/tools/*` and `/calls/ingest`.
- **Goal**: call_outcome in {meeting_booked, proposal_sent, callback_booked}
- **Voicemail** and silence nudge configured; max call 10 minutes.

## First loop on the platform (9 Oct, text test)
1. Test conversation as an engaged seller: `flag_sales_ready` and `get_demand_pitch` reached our service; the seller
   agreed to Thursday 5 pm.
2. Audit graded it **Fatal**: the bot called `book_callback` in the same turn as `end_interaction`; the platform ran
   only the end, so no booking was made although the bot said it was.
3. Fix: rule "Booking before closing" in the prompt. Re-test: callback reached the queue.
4. Second finding: the bot voiced "5.2 Cr" as "saadhe paanch crore". Fix at the source: the pitch tool now returns
   speech-safe amounts ("5 crore 21 lakh").

The tunnel URL in the tools is a Cloudflare quick tunnel and changes on restart; re-point the five tools when it does.
