# The live Sarvam agents

- **Female**: `IndiaMART Seller Growth Call` · `IndiaMART-S-31ca33eb-e1ce` · voice Sarika · agent_name Sarika
- **Male**: `IndiaMART Seller Growth Call - Rahul` · `IndiaMART-S-8c69b7be-a89a` · voice Aditya (Sales Agent) · agent_name Rahul
- Same prompt and tools on both. `service/voices.py` picks the voice per seller from results (50/50 until 5 audited
  calls per voice in a state + category segment, then the better voice, 20% kept for re-checking). `GET /route/{glid}`.
- **Deployed prompt**: `agent/sarvam_prompt.md`; `sarvam_prompt_v3.md` is the previous flow.
- **Workspace constraint**: one state only; the flow is sections of one prompt.

## v4 flow (female agent version 5, male version 2)
identify → hook (buyers who want his product in his city) → discovery (≤2) → `assess_engagement`
(server scores: asked_question +2, detailed_answers +2, shared_pain +1, positive_tone +1, one_word_answers −1,
burned_before −1, busy_or_irritated −3; presales at ≥3 and not busy; asking price = presales) →
meeting_only: fix day/time/place · presales: "do minute aur?" → explain (no price) → WhatsApp proposal line → fix meeting
→ book_callback → "Aapka samay dene ke liye dhanyavaad, aapke dhande mein khoob tarakki ho!"

Per-call variables: glid, seller_name, agent_name, greeting (by PIN-code state), city, product, meeting_place,
seller_md (+ remembered facts), persona, playbook, category_playbook, hook. Written mid-call: live_instruction, sales_path.

## Company rules the audit checks
`audit/grade.py` POLICY: never quote a plan price on the call, never use IndiaMART jargon (BuyLead, TrustSEAL),
never ask how the seller will pay. Breaking one is Fatal; each broken rule is its own problem, fix and replay.
`python3 audit/demo_reset.py` starts a clean round (backs up the database first).

Tunnel URL in the tools is a Cloudflare quick tunnel and changes on restart; re-point the tools when it does.
