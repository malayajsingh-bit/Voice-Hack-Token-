"""Point every test at a scratch sqlite file and expose the repo on sys.path."""
import os
import pathlib
import sys
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "service"))
sys.path.insert(0, str(ROOT / "audit"))


@pytest.fixture()
def tmp_db(monkeypatch, tmp_path):
    """Each test gets its own sqlite file so tool_log, queue and seller_ctx start empty."""
    import config
    import store

    db_path = tmp_path / "audit.db"
    monkeypatch.setattr(config, "DB", db_path)
    monkeypatch.setattr(config, "DATA", tmp_path)
    # force reconnection on next access by resetting the module-level cache (there is none,
    # store.db() opens a fresh connection per call) — nothing else to do.
    with store.db():                                                       # creates the schema
        pass
    yield store
