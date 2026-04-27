from datetime import datetime, timezone
from zoneinfo import ZoneInfo

BKK = ZoneInfo("Asia/Bangkok")


def utcnow() -> datetime:
    """Naive UTC datetime — matches the default DateTime column type on round-trip.

    Storage is implicitly UTC; v2 can switch to TIMESTAMPTZ if multi-zone support is needed.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def bkk_hms(dt: datetime) -> str:
    """Format a (naive UTC) datetime as `HH:MM:SS` in Asia/Bangkok time.

    The shop dashboard renders all wall-clock times in BKK so staff don't
    have to do timezone math when reading the live feed.
    """
    return dt.replace(tzinfo=timezone.utc).astimezone(BKK).strftime("%H:%M:%S")
