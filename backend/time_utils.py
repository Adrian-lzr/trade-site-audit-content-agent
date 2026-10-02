"""Canonical UTC handling for API boundaries and persisted timestamps."""

from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None, *, assume_utc_for_legacy: bool = True) -> datetime | None:
    """Return an aware UTC value; legacy naive database values mean UTC.

    New external input must call ``require_aware_utc`` instead.  The explicit
    legacy switch keeps historical SQLite rows comparable without guessing a
    local timezone.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        if not assume_utc_for_legacy:
            raise ValueError("timestamp must include timezone")
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def require_aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include timezone")
    return value.astimezone(timezone.utc)
