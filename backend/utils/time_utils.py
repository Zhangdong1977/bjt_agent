"""Timezone-aware UTC helpers.

Why this module exists
----------------------
Historically the backend wrote ``datetime.utcnow()`` (a *naive* datetime, no
tzinfo) into PostgreSQL ``timestamp without time zone`` columns. The JSON API
then returned these as bare strings like ``"2026-06-20T10:00:00"`` (no offset).
The browser's ``new Date("2026-06-20T10:00:00")`` interprets a tz-less string
as *local* time, so for users in UTC+8 the displayed time was 8 hours behind.

Fix: every value written to (and therefore read from) the DB is now
timezone-aware UTC. Postgres columns are ``timestamptz``. Pydantic then
serializes aware datetimes with an explicit ``+00:00`` offset, and the browser
correctly converts that to local time. Front-end code needs no change.

JWT ``exp`` claims are intentionally NOT migrated here — per RFC 7519 they are
numeric UTC epoch seconds and are handled separately in ``api/deps.py``.
"""

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime.

    Always use this instead of ``datetime.utcnow()`` (naive) for any value that
    will be persisted or returned to clients.
    """
    return datetime.now(timezone.utc)


def ensure_utc_aware(value: datetime | None) -> datetime | None:
    """Return a timezone-aware UTC datetime.

    Some existing databases still return UTC timestamps as naive datetimes even
    when the application writes aware values. Treat naive values as UTC so time
    arithmetic does not fail with mixed aware/naive datetimes.
    """
    if value is None:
        return None
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def utc_seconds_between(start: datetime, end: datetime) -> int:
    """Return elapsed seconds between two UTC datetimes."""
    start_utc = ensure_utc_aware(start)
    end_utc = ensure_utc_aware(end)
    if start_utc is None or end_utc is None:
        return 0
    return int((end_utc - start_utc).total_seconds())
