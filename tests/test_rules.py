"""Step 3: rule-based pre-LLM checks catch the failures the LLM grader missed on
phone calls 2 and 4 in docs/call-log.md."""
import rules
import store


def test_number_mismatch_catches_32_hazaar_when_tool_said_31000(tmp_db):
    """Phone calls 2 and 4: the tool returned aov 31,000 and the bot voiced
    'average order 32 hazaar'. The LLM grader missed this twice."""
    cid = "test-num-mismatch"
    tmp_db.run("INSERT INTO tool_log VALUES (?,?,?,?,?,?,?,?)",
               (store.nid(), cid, "4213238", "get_demand_pitch",
                store.J({"glid": "4213238"}),
                store.J({"available": True, "buyers_month": 1500, "aov": 31000,
                         "aov_h": "31,000", "say": "average order 31 hazaar rupaye"}),
                "ok", store.now()))
    transcript = (
        "Bot: Namaste, Shri Shyam Sanitation se baat kar raha hoon.\n"
        "Seller: Haan boliye.\n"
        "Bot: Sir aapki category mein average order 32 hazaar rupaye ka hai.\n"
        "Seller: Acha."
    )
    v = rules.check(cid, transcript)
    assert v["grade"] == "Fatal", v
    assert "32000" in v["reason"] or "32 " in v["reason"] or "32" in v["reason"]
    assert "wrong_fact" in v["flags"]


def test_number_matches_when_bot_echoes_tool(tmp_db):
    cid = "test-num-match"
    tmp_db.run("INSERT INTO tool_log VALUES (?,?,?,?,?,?,?,?)",
               (store.nid(), cid, "1", "get_demand_pitch",
                store.J({"glid": "1"}),
                store.J({"aov": 31000, "aov_h": "31,000"}), "ok", store.now()))
    transcript = (
        "Bot: Average order 31 hazaar rupaye.\n"
        "Seller: Theek."
    )
    v = rules.check(cid, transcript)
    assert v["grade"] is None, v


def test_number_matches_when_seller_said_it(tmp_db):
    """Numbers the seller themselves introduced must be allowed back."""
    cid = "test-num-seller"
    transcript = (
        "Seller: Mera turnover 50 lakh ka hai.\n"
        "Bot: Theek sir, 50 lakh ke turnover ke liye TrustSEAL fit baithta hai."
    )
    v = rules.check(cid, transcript, tool_log=[])
    assert v["grade"] is None, v


def test_booking_and_closing_in_same_turn_is_fatal(tmp_db):
    """Phone call 2 and 4: 'book kar diya ... dhanyawad, call rakhti hoon' in one bot
    turn; the platform ran only the end, so the booking never saved."""
    cid = "test-book-close"
    tmp_db.run("INSERT INTO tool_log VALUES (?,?,?,?,?,?,?,?)",
               (store.nid(), cid, "1", "book_callback",
                store.J({"glid": "1", "when": "Sunday 10 am"}),
                store.J({"ok": True}), "ok", store.now()))
    transcript = (
        "Bot: Sunday 10 am par call book kar diya hai, dhanyawad sir, call rakhti hoon."
    )
    v = rules.check(cid, transcript)
    assert v["grade"] == "Fatal", v
    assert "same turn" in v["reason"]


def test_said_booked_but_no_tool_call_is_fatal(tmp_db):
    cid = "test-booked-no-tool"
    transcript = "Bot: Theek hai sir, kal 11 baje ke liye callback book kar diya hai."
    v = rules.check(cid, transcript, tool_log=[])
    assert v["grade"] == "Fatal", v
    assert "never called" in v["reason"]


def test_pitch_after_clear_no_is_fatal(tmp_db):
    cid = "test-pitch-after-no"
    transcript = (
        "Bot: Sir TrustSEAL plan 60,000 ka hai.\n"
        "Seller: Mujhe nahi chahiye, call mat karo.\n"
        "Bot: Sir ek minute, MDC plan 35,000 ka bhi hai, aapko BuyLeads milenge."
    )
    v = rules.check(cid, transcript, tool_log=[])
    assert v["grade"] == "Fatal", v
    assert "kept_pitching_after_no" in v["flags"]


def test_repeated_sentence_is_flagged_not_fatal(tmp_db):
    cid = "test-repeat"
    transcript = (
        "Bot: Hamara TrustSEAL plan aapke business ke liye bahut faydemand hai.\n"
        "Seller: Hmm.\n"
        "Bot: Hamara TrustSEAL plan aapke business ke liye bahut faydemand hai."
    )
    v = rules.check(cid, transcript, tool_log=[])
    assert v["grade"] is None, v
    assert "loop" in v["flags"]


def test_clean_call_passes(tmp_db):
    cid = "test-clean"
    tmp_db.run("INSERT INTO tool_log VALUES (?,?,?,?,?,?,?,?)",
               (store.nid(), cid, "1", "book_callback", store.J({"when": "Monday 3 pm"}),
                store.J({"ok": True}), "ok", store.now()))
    transcript = (
        "Bot: Namaste, Sajawat Kitchen se baat ho rahi hai?\n"
        "Seller: Haan boliye.\n"
        "Bot: Aapke liye Monday 3 pm par callback book kar diya hai.\n"
        "Seller: Theek hai.\n"
        "Bot: Dhanyawad, aapka din shubh ho."
    )
    v = rules.check(cid, transcript)
    assert v["grade"] is None, v
