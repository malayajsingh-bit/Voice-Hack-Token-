"""d: a persona that failed to build must not be cached. Ported from PR #1 (Ansh Goyal)."""
import context
import llm


def test_failed_persona_is_not_cached(tmp_db, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("gateway down")
    monkeypatch.setattr(llm, "chat_json", boom)
    monkeypatch.setattr(context, "load_raw", lambda g: {"glid": g, "company_name": "Test Traders"})
    r = context.build("55")
    assert "default persona" in r["persona"].get("why", "")
    assert tmp_db.one("SELECT 1 AS x FROM seller_ctx WHERE glid='55'") is None


def test_variables_work_without_a_cached_row(tmp_db, monkeypatch):
    """With nothing cached (persona failed), building call variables must still succeed."""
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(context, "load_raw", lambda g: {"glid": g, "company_name": "Test Traders"})
    v = context.variables("56")
    assert v["seller_name"] == "Test Traders" and v["glid"] == "56"


def test_real_persona_is_cached(tmp_db, monkeypatch):
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: ({"language": "hindi", "why": "test"}, 0.0))
    monkeypatch.setattr(context, "load_raw", lambda g: {"glid": g, "company_name": "Test Traders"})
    monkeypatch.setattr(context, "assign_category", lambda g, raw: None)
    context.build("57")
    assert tmp_db.one("SELECT 1 AS x FROM seller_ctx WHERE glid='57'") is not None
