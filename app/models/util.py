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


def bkk_feed_time_short(dt: datetime) -> str:
    """Compact feed-row label for table columns where the full
    `bkk_feed_time` (weekday + date + HH:MM:SS) is too wide. Renders
    just `HH:MM` for today and `25 เม.ย. HH:MM` once the row crosses
    midnight, so staff still get day context without seconds."""
    bkk = dt.replace(tzinfo=timezone.utc).astimezone(BKK)
    today = datetime.now(timezone.utc).astimezone(BKK).date()
    if bkk.date() == today:
        return bkk.strftime("%H:%M")
    return f"{bkk.day} {_THAI_MONTH_SHORT[bkk.month - 1]} {bkk.strftime('%H:%M')}"
