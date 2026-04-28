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


_THAI_WEEKDAY = ("จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์")
_THAI_MONTH_SHORT = (
    "ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
    "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค.",
)


def bkk_feed_time(dt: datetime) -> str:
    """Format a (naive UTC) datetime as the dashboard feed-row label
    `ศุกร์ 25 เม.ย. · HH:MM:SS` in Asia/Bangkok time. Used by the S3 dock
    so each row carries enough context to read at a glance even when the
    feed runs across midnight.
    """
    bkk = dt.replace(tzinfo=timezone.utc).astimezone(BKK)
    return (
        f"{_THAI_WEEKDAY[bkk.weekday()]} "
        f"{bkk.day} {_THAI_MONTH_SHORT[bkk.month - 1]} "
        f"· {bkk.strftime('%H:%M:%S')}"
    )


def bkk_short_date(dt: datetime) -> str:
    """Short Thai date `25 เม.ย.` in Asia/Bangkok — used on campaign cards
    where time-of-day is noise compared to the day of send."""
    bkk = dt.replace(tzinfo=timezone.utc).astimezone(BKK)
    return f"{bkk.day} {_THAI_MONTH_SHORT[bkk.month - 1]}"
