from datetime import datetime, timezone


def utcnow() -> datetime:
    """Naive UTC datetime — matches the default DateTime column type on round-trip.

    Storage is implicitly UTC; v2 can switch to TIMESTAMPTZ if multi-zone support is needed.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
