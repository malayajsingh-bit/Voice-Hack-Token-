"""Step 1: calls are trackable. Verify id-mapping, URL-key auth, and tool_log."""
from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "DB", tmp_path / "audit.db")
    monkeypatch.setattr(config, "DATA", tmp_path)
    # reload modules so they pick up the patched config
    import importlib
    import store
    importlib.reload(store)
    import app
    importlib.reload(app)
    return app, TestClient(app.app)


def test_tool_call_is_logged(monkeypatch, tmp_path):
    import config as cfg
    monkeypatch.setattr(cfg, "DB", tmp_path / "audit.db")
    monkeypatch.setattr(cfg, "DATA", tmp_path)
    import importlib, store, tools, app
    importlib.reload(store); importlib.reload(tools); importlib.reload(app)
    store.run("INSERT INTO seller_ctx VALUES (?,?,?,?,?,?)",
              ("1", "name: s", store.J({}), store.J({"line": "x"}), store.J({"glid": "1"}), store.now()))
    c = TestClient(app.app)
    r = c.post("/tools/set_persona", json={"glid": "1", "call_id": "cid-abc", "signal": "rushed"})
    assert r.status_code == 200
    rows = store.rows("SELECT * FROM tool_log WHERE call_id='cid-abc'")
    assert len(rows) == 1
    assert rows[0]["tool"] == "set_persona"
    assert rows[0]["status"] == "ok"


def test_ingest_maps_interaction_id_to_our_call_id(monkeypatch, tmp_path):
    import config as cfg
    monkeypatch.setattr(cfg, "DB", tmp_path / "audit.db")
    monkeypatch.setattr(cfg, "DATA", tmp_path)
    import importlib, store, app, grade
    importlib.reload(store); importlib.reload(app)
    # The ingest handler spawns a background audit thread that would otherwise
    # try to call the LLM gateway. Stub it so the test is hermetic.
    monkeypatch.setattr(grade, "grade", lambda cid, t: {"grade": None})
    # a call started earlier
    store.run("INSERT INTO call VALUES (?,?,?,?,?,?,?,?,?,?)",
              ("our-cid-1", "4213238", "live", store.now(), None, "", None, "A",
               store.J({"attempt_id": "sarvam-iid-xyz"}), store.now()))
    store.run("INSERT INTO interaction_map VALUES (?,?,?)",
              ("sarvam-iid-xyz", "our-cid-1", store.now()))
    c = TestClient(app.app)
    r = c.post("/calls/ingest",
               json={"interaction_id": "sarvam-iid-xyz",
                     "metadata": {"glid": "4213238", "call_id": "our-cid-1"},
                     "transcript": "Bot: hi\nSeller: hi back", "duration": 90})
    assert r.status_code == 200
    assert r.json()["call_id"] == "our-cid-1"
    row = store.one("SELECT * FROM call WHERE id='our-cid-1'")
    assert row["transcript"].startswith("Bot: hi")
    assert row["duration"] == 90


def test_tunnel_guard_accepts_url_key(monkeypatch, tmp_path):
    import os
    monkeypatch.setenv("TOOL_KEY", "secret-abc")
    import config as cfg
    monkeypatch.setattr(cfg, "DB", tmp_path / "audit.db")
    monkeypatch.setattr(cfg, "DATA", tmp_path)
    import importlib, store, app
    importlib.reload(store); importlib.reload(app)
    c = TestClient(app.app)
    # arriving through the tunnel without a key -> 401
    r = c.post("/calls/ingest", json={"call_id": "x"},
               headers={"cf-connecting-ip": "1.2.3.4"})
    assert r.status_code == 401
    # with ?k= -> 200
    r = c.post("/calls/ingest?k=secret-abc",
               json={"call_id": "x", "transcript": ""},
               headers={"cf-connecting-ip": "1.2.3.4"})
    assert r.status_code == 200
    # with x-tool-key still works
    r = c.post("/calls/ingest",
               json={"call_id": "y", "transcript": ""},
               headers={"cf-connecting-ip": "1.2.3.4", "x-tool-key": "secret-abc"})
    assert r.status_code == 200
    # dashboard (no cf-connecting-ip) is unaffected
    r = c.get("/")
    assert r.status_code == 200
