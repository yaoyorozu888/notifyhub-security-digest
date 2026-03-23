from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from notifyhub_digest.models import Source
from notifyhub_digest.rss import RawEntry
from notifyhub_digest.runner import run_digest
from notifyhub_digest.timeutils import compute_daily_window

def _patch_runner(monkeypatch, *, send_success: bool) -> list[bool]:
    send_calls: list[bool] = []

    src = Source(name="Test", feed_url="https://example.com/feed", enabled=True)
    monkeypatch.setattr("notifyhub_digest.runner.load_sources", lambda _p: [src])
    monkeypatch.setattr("notifyhub_digest.runner.iter_enabled_sources", lambda sources: list(sources))

    run_at_iso = "2026-01-12T06:00:00+09:00"
    window = compute_daily_window(datetime.fromisoformat(run_at_iso))

    entry = RawEntry(
        entry_id="abcd1234",
        title="Test Title",
        link="https://example.com/article",
        published_at_utc=window.start_utc + timedelta(minutes=1),
        summary=None,
    )
    monkeypatch.setattr(
        "notifyhub_digest.runner.fetch_feed_entries",
        lambda _client, _src, user_agent: [entry],
    )

    monkeypatch.setenv("ACS_EMAIL_CONNECTION_STRING", "endpoint=https://example.communication.azure.com/;accessKey=fake")
    monkeypatch.setenv("ACS_EMAIL_SENDER", "donotreply@example.com")
    monkeypatch.setenv("ACS_EMAIL_TO", "to@example.com")

    def _send(**_kwargs):
        send_calls.append(True)
        return send_success

    monkeypatch.setattr("notifyhub_digest.runner.send_acs_email", _send)

    return send_calls


def test_runner_marks_read_only_when_email_succeeds(tmp_path: Path, monkeypatch):
    send_calls = _patch_runner(monkeypatch, send_success=True)

    run_digest(
        out_dir=tmp_path / "out",
        sources_path=tmp_path / "sources.json",
        run_at_iso="2026-01-12T06:00:00+09:00",
        send_email=True,
    )

    assert send_calls == [True]
    assert (tmp_path / "out" / "digest" / "2026" / "01" / "12" / "manifest.json").exists()


def test_runner_does_not_mark_read_when_email_fails(tmp_path: Path, monkeypatch):
    send_calls = _patch_runner(monkeypatch, send_success=False)

    run_digest(
        out_dir=tmp_path / "out",
        sources_path=tmp_path / "sources.json",
        run_at_iso="2026-01-12T06:00:00+09:00",
        send_email=True,
    )

    assert send_calls == [True]
    assert (tmp_path / "out" / "digest" / "2026" / "01" / "12" / "manifest.json").exists()
