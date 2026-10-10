"""g: every tool call is logged, and a call's tool calls are found by GLID and time."""
from fastapi.testclient import TestClient


def test_tool_endpoint_writes_log(tmp_db):
    import app
    client = TestClient(app.app)
    r = client.post("/tools/book_callback", json={"glid": "4213238", "when": "kal 5 baje"})
    assert r.status_code == 200
    rows = tmp_db.rows("SELECT * FROM tool_log WHERE glid='4213238'")
    assert len(rows) == 1 and rows[0]["tool"] == "book_callback" and rows[0]["status"] == "ok"
    assert tmp_db.L(rows[0]["response"], {}).get("ok") is True


def _log(store, glid, tool, at):
    store.run("INSERT INTO tool_log VALUES (?,?,?,?,?,?,?,?)",
              (store.nid(), "", glid, tool, "{}", "{}", "ok", at))


def test_tool_calls_matched_by_glid_and_time(tmp_db):
    tmp_db.run("INSERT INTO call VALUES (?,?,?,?,?,?,?,?,?,?)",
               ("c1", "4213238", "live", "2026-10-10 14:39:13", 46, "t", None, "A", "{}", "2026-10-10 14:40:00"))
    _log(tmp_db, "4213238", "set_persona", "2026-10-10 14:39:30")       # during the call
    _log(tmp_db, "4213238", "book_callback", "2026-10-10 14:40:05")     # just after it ended
    _log(tmp_db, "4213238", "book_callback", "2026-10-10 18:00:00")     # a different call
    _log(tmp_db, "9999999", "set_persona", "2026-10-10 14:39:40")       # another seller
    got = [t["tool"] for t in tmp_db.tool_calls_for("c1")]
    assert got == ["set_persona", "book_callback"]


def test_tool_calls_prefer_call_id(tmp_db):
    tmp_db.run("INSERT INTO call VALUES (?,?,?,?,?,?,?,?,?,?)",
               ("c2", "4213238", "live", "2026-10-10 14:39:13", 46, "t", None, "A", "{}", "2026-10-10 14:40:00"))
    tmp_db.run("INSERT INTO tool_log VALUES (?,?,?,?,?,?,?,?)",
               (tmp_db.nid(), "c2", "4213238", "flag_risk", "{}", "{}", "ok", "2026-10-10 20:00:00"))
    assert [t["tool"] for t in tmp_db.tool_calls_for("c2")] == ["flag_risk"]
