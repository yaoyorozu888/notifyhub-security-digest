from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
UTC = ZoneInfo("UTC")


@dataclass(frozen=True)
class TimeWindow:
    start_utc: datetime
    end_utc: datetime

    @property
    def start_jst(self) -> datetime:
        return self.start_utc.astimezone(JST)

    @property
    def end_jst(self) -> datetime:
        return self.end_utc.astimezone(JST)


def compute_daily_window(run_at_jst: datetime) -> TimeWindow:
    """前日06:00(JST) <= published < 当日06:00(JST)

    run_at_jst は JST の aware datetime を想定。
    """

    if run_at_jst.tzinfo is None:
        raise ValueError("run_at_jst must be timezone-aware")

    anchor = datetime.combine(run_at_jst.date(), time(6, 0, 0), tzinfo=JST)
    if run_at_jst < anchor:
        end = anchor - timedelta(days=1)
    else:
        end = anchor

    start = end - timedelta(days=1)
    return TimeWindow(start_utc=start.astimezone(UTC), end_utc=end.astimezone(UTC))
