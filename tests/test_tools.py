"""Step 2: tools are safe (no mid-call LLM, dedupe bookings, no zero-pitch,
no permanent failed persona)."""
import tools


def _seed_ctx(store, glid, demand=None, persona=None):
    store.run("INSERT OR REPLACE INTO seller_ctx VALUES (?,?,?,?,?,?)",
              (str(glid), f"name: seller {glid}",
               store.J(persona or {"language": "hinglish-hindi-leaning"}),
               store.J(demand or {}),
               store.J({"glid": glid}), store.now()))


def test_book_callback_dedupes_within_window(tmp_db):
    glid = "4213238"
    a = tools.book_callback(glid, "call-1", "Sunday 10 am")
    b = tools.book_callback(glid, "call-1", "Sunday 10 am")
    assert a["duplicate"] is False
    assert b["duplicate"] is True
    rows = tmp_db.rows("SELECT * FROM queue WHERE kind='callback' AND glid=?", (glid,))
    assert len(rows) == 1, "a repeat booking inside the window must not create a second queue row"


def test_book_callback_outside_window_creates_new(tmp_db):
    """An earlier booking that falls OUTSIDE the dedup window must not block a new one."""
    glid = "4213239"
    old = "2020-01-01 00:00:00"
    tmp_db.run("INSERT INTO queue VALUES (?,?,?,?,?,?,?,?)",
               (tmp_db.nid(), "callback", "call-old", glid, "callback old",
                tmp_db.J({}), old, None))
    r = tools.book_callback(glid, "call-new", "Monday 11 am")
    assert r["duplicate"] is False
    rows = tmp_db.rows("SELECT * FROM queue WHERE kind='callback' AND glid=?", (glid,))
    assert len(rows) == 2


def test_get_demand_pitch_returns_unavailable_when_empty(tmp_db):
    _seed_ctx(tmp_db, "99", demand={"buyers_month": 0, "value_month": 0, "aov": 0,
                                     "bl_city_30d": 0, "monthly_est": 0,
                                     "weekly_buyleads": 21, "close_rate": 0.1, "line": ""})
    r = tools.get_demand_pitch("99")
    assert r["available"] is False
    assert "proposal" in r["say"].lower() or "whatsapp" in r["say"].lower()
    assert "0" not in r.get("say", "").split()                             # don't quote zeros


def test_get_demand_pitch_returns_data_when_present(tmp_db):
    _seed_ctx(tmp_db, "10", demand={"buyers_month": 1500, "value_month": 52_100_000,
                                     "aov": 31000, "bl_city_30d": 190,
                                     "monthly_est": 300000, "weekly_buyleads": 21,
                                     "close_rate": 0.1, "value_month_h": "5.2 Cr",
                                     "aov_h": "31,000", "monthly_est_h": "3 Lakh",
                                     "line": "test line 31 hazaar"})
    r = tools.get_demand_pitch("10")
    assert r["available"] is True
    assert r["aov"] == "31,000"


def test_tools_never_invoke_llm(tmp_db, monkeypatch):
    """None of the in-call tools may call context.build (which triggers an LLM)."""
    import context

    def boom(*a, **k):
        raise AssertionError("context.build must not be invoked from a tool handler")

    monkeypatch.setattr(context, "build", boom)
    _seed_ctx(tmp_db, "7")
    tools.get_seller_context("7")
    tools.set_persona("7", "cid", "rushed")
    tools.flag_sales_ready("7", "cid", "asked price", 0.9)
    tools.get_demand_pitch("7")
    tools.flag_risk("7", "cid", "loop")


def test_context_build_does_not_cache_persona_failure(tmp_db, monkeypatch):
    """A one-off LLM error must not freeze a default persona for the GLID (issue #10)."""
    import context
    import llm

    def boom(*a, **k):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(llm, "chat_json", boom)
    monkeypatch.setattr(context, "load_raw", lambda g: {"glid": g, "company_name": "Test"})
    r = context.build("55")
    assert "default persona" in r["persona"].get("why", "")
    assert tmp_db.one("SELECT 1 FROM seller_ctx WHERE glid='55'") is None, \
        "a failed persona must not be cached"
