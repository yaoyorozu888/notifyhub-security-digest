from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from notifyhub_digest.timeutils import compute_daily_window

JST = ZoneInfo("Asia/Tokyo")


def test_window_at_0600_is_previous_day_to_same_day():
    run_at = datetime(2026, 1, 12, 6, 0, 0, tzinfo=JST)
    w = compute_daily_window(run_at)
    assert w.start_jst.isoformat() == "2026-01-11T06:00:00+09:00"
    assert w.end_jst.isoformat() == "2026-01-12T06:00:00+09:00"


def test_window_before_0600_uses_previous_anchor():
    run_at = datetime(2026, 1, 12, 5, 59, 0, tzinfo=JST)
    w = compute_daily_window(run_at)
    assert w.start_jst.isoformat() == "2026-01-10T06:00:00+09:00"
    assert w.end_jst.isoformat() == "2026-01-11T06:00:00+09:00"
