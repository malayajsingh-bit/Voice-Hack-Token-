# Demo script — 5 to 7 minutes

0:00 **The problem in one screen.** Today: a spreadsheet with ~3% of calls. "Most failures go unseen."

0:30 **Overview tile row.** Calls audited 100%. Agreement with human auditors κ = _…_. Fatal rate _…_%.
Sales-ready found _…_. Point at "time to tested fix".

1:15 **One call.** Open a Fatal call. Read the one-line reason and the failure turn. Show the flags.
Then one Non-Fatal that ended in a booked meeting with `sales_ready`.

2:00 **Root causes.** Ranked list. Top cause: "_bot ignores pushback and continues the script_", 5 calls,
impact 20. Open examples.

2:45 **The fix.** Click Propose. Show the diff: one rule changed. Rationale and the test it suggests.
Click Approve → experiment created at 10%.

3:30 **Live call, before and after.** Call the simulated seller "rushed Ramesh" with the current prompt:
bot keeps pitching, Ramesh hangs up. Switch to variant B, call again: bot offers the two-minute version,
`set_persona(rushed)` fires, callback booked.

5:00 **Pre-sales on the fly.** Call "price objector Priya". Objection handled from the playbook; she asks
"kitna business ho sakta hai" → `flag_sales_ready` → permission → `get_demand_pitch` numbers → proposal on
WhatsApp. Show the pre-sales queue entry.

6:00 **Experiment result.** A vs B on the cause: _…_% → _…_%, p = _…_. Promote. Prompt v2 pushed to the agent.

6:30 **Close.** Three numbers: 100% audited, κ _…_, fix tested in _…_ minutes. "The loop finds revenue, not just bugs."
