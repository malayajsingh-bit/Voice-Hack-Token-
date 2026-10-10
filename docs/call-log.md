# Real calls on Sarvam (9–10 Oct 2026)

Agent `IndiaMART Seller Growth Call` (`IndiaMART-S-31ca33eb-e1ce`). Test seller in every call: Shri Shyam
Sanitation, GLID 4213238 (per-call variables are not injected by test calls; the agent's defaults are used).

## Text chats (Sarvam playground)
1. **Engaged seller, asks price, agrees to Thursday 5 pm.** flag_sales_ready and get_demand_pitch reached our
   service. The bot called book_callback in the same turn as end_interaction; the platform ran only the end, so no
   booking was saved although the bot said it was. Audit: **Fatal**, sales_ready. Fix: prompt rule "Booking before
   closing".
2. **Same script after the fix.** The callback reached our queue. Fix confirmed. Bot voiced "5.2 Cr" as
   "saadhe paanch crore"; fix at the source: the pitch tool now returns "5 crore 21 lakh".

## Phone calls
| # | number | length | result | what we learned |
|---|---|---|---|---|
| 1 | own | 31 s | line dropped after greeting (websocket 1006) | platform/line issue, not the bot |
| 2 | own | 3 min 13 s | meeting booked, tomorrow 3 pm | booking fix holds on a real call; spoken amounts correct; said average order "32 hazaar" (tool: 31); argued "our records say" when the seller named another product; audit graded Non-Fatal and missed the number |
| 3 | second | 44 s | wrong person, handled | bot took "ji Shyam, kahaan laga diya?" as identity confirmed and started the pitch before recovering |
| 4 | third | 3 min 14 s | meeting booked, this Sunday 10 am | good discovery and date correction; again "32 hazaar"; contradicted the seller's own figure; book + end in one turn again; our service received the booking twice |

Sarvam's own goal check (call_outcome in meeting / proposal / callback) passed on calls 2 and 4.
set_persona has not fired in any real call yet.

## Open fixes, in priority order
1. Numbers check in the audit: compare every number the bot spoke with the tool's output; a mismatch is Fatal.
   The LLM grader missed it twice.
2. Deduplicate bookings in the service (same GLID within 2 minutes).
3. Prompt: say amounts exactly as returned; acknowledge, never contradict, the seller's own figures; identity is
   confirmed only by an explicit yes; product corrections go to flag_risk(wrong_requirement).
4. Close the integration gap: Sarvam webhook to /calls/ingest (or sync.py with a Conversations API key) and the
   dashboard promote button.
