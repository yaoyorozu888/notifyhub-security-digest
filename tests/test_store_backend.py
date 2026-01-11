from __future__ import annotations

from pathlib import Path

from notifyhub_digest.store import ReadStore, get_read_store


def test_default_backend_is_local(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("READ_STORE_BACKEND", raising=False)
    store = get_read_store(tmp_path)
    assert isinstance(store, ReadStore)
