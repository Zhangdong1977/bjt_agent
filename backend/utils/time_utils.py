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
