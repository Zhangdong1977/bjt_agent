from datetime import datetime, timezone

from backend.utils.time_utils import ensure_utc_aware, utc_seconds_between


def test_ensure_utc_aware_treats_naive_datetime_as_utc():
    value = datetime(2026, 6, 28, 9, 45, 39)

    result = ensure_utc_aware(value)

    assert result == datetime(2026, 6, 28, 9, 45, 39, tzinfo=timezone.utc)


def test_utc_seconds_between_accepts_mixed_naive_and_aware_datetimes():
    start = datetime(2026, 6, 28, 9, 45, 11)
    end = datetime(2026, 6, 28, 9, 45, 39, tzinfo=timezone.utc)

    assert utc_seconds_between(start, end) == 28
