"""Tool safety: one booking per seller per window, never pitch zeros.
Ported from PR #1 (Ansh Goyal) and adapted to this branch."""
import tools


def _seed_ctx(store, glid, demand=None, persona=None):
    store.run("INSERT OR REPLACE INTO seller_ctx VALUES (?,?,?,?,?,?)",
              (str(glid), f"name: seller {glid}",
               store.J(persona or {"language": "hinglish-hindi-leaning"}),
               store.J(demand or {}), store.J({"glid": glid, "company_name": f"Seller {glid}"}), store.now()))


FULL = {"buyers_month": 1658, "value_month": 52_100_000, "aov": 31000, "bl_city_30d": 420,
        "monthly_est": 264000, "weekly_buyleads": 21, "close_rate": 0.1, "value_month_h": "5 crore 21 lakh",
        "aov_h": "31,000", "monthly_est_h": "2 lakh 64 hazaar", "line": "1,658 buyers, average order 31 hazaar"}
ZERO = {"buyers_month": 0, "value_month": 0, "aov": 0, "bl_city_30d": 0, "monthly_est": 0,
        "weekly_buyleads": 21, "close_rate": 0.1, "line": "0 buyers, 0 rupaye"}


# --- a: duplicate bookings -------------------------------------------------
def test_book_callback_dedupes_within_window(tmp_db):
    a = tools.book_callback("4213238", "call-1", "Sunday 10 am")
    b = tools.book_callback("4213238", "call-1", "This Sunday at 10 AM")
    assert a["duplicate"] is False and b["duplicate"] is True
    assert len(tmp_db.rows("SELECT * FROM queue WHERE kind='callback' AND glid='4213238'")) == 1


def test_book_callback_outside_window_creates_new(tmp_db):
    tmp_db.run("INSERT INTO queue VALUES (?,?,?,?,?,?,?,?)",
               (tmp_db.nid(), "callback", "old", "4213239", "callback old", tmp_db.J({}), "2020-01-01 00:00:00", None))
    assert tools.book_callback("4213239", "new", "Monday 11 am")["duplicate"] is False
    assert len(tmp_db.rows("SELECT * FROM queue WHERE kind='callback' AND glid='4213239'")) == 2


def test_book_callback_other_seller_not_blocked(tmp_db):
    tools.book_callback("111", "c1", "kal 5 baje")
    assert tools.book_callback("222", "c2", "kal 5 baje")["duplicate"] is False


# --- c: never pitch zeros ----------------------------------------------------
def test_demand_pitch_unavailable_when_all_zero(tmp_db):
    _seed_ctx(tmp_db, "99", demand=ZERO)
    r = tools.get_demand_pitch("99")
    assert r["available"] is False
    assert "0" not in r["say"].split() and "whatsapp" in r["say"].lower()


def test_demand_pitch_returns_numbers_when_present(tmp_db):
    _seed_ctx(tmp_db, "10", demand=FULL)
    r = tools.get_demand_pitch("10")
    assert r["available"] is True and r["aov"] == "31,000" and "31 hazaar" in r["say"]


def test_sales_ready_does_not_pass_a_zero_pitch(tmp_db):
    _seed_ctx(tmp_db, "98", demand=ZERO)
    r = tools.flag_sales_ready("98", "c", "asked price", 0.9)
    assert r["state"] == "presales" and "pitch" not in r and r["available"] is False


def test_engagement_presales_at_threshold(tmp_db):
    r = tools.assess_engagement("1", "c", ["asked_question", "shared_pain"])
    assert r["sales_path"] == "presales" and r["score"] == 3


def test_engagement_busy_blocks_presales(tmp_db):
    r = tools.assess_engagement("1", "c", ["asked_question", "detailed_answers", "busy_or_irritated"], asked_price=True)
    assert r["sales_path"] == "meeting_only"


def test_engagement_asked_price_shortcut(tmp_db):
    assert tools.assess_engagement("1", "c", [], asked_price=True)["sales_path"] == "presales"
