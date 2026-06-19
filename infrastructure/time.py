"""
infrastructure/time.py
Single source of truth for time. Always timezone-aware UTC.

Replaces datetime.utcnow() (deprecated in 3.12, returns naive datetimes that
break comparisons and downtime math when mixed with aware ones).
"""
from datetime import datetime, timezone
from typing import Optional


def utcnow() -> datetime:
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """
    Coerce a datetime to timezone-aware UTC.

    Old rows persisted before this migration are naive; we assume they were
    UTC (the codebase always wrote UTC) and attach the tzinfo so comparisons
    with utcnow() never raise 'can't compare naive and aware'.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
