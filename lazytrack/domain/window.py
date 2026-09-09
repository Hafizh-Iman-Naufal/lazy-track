from datetime import date, timedelta

# 8 lookback weeks + Mon–Sun remainder of the current week
MAX_CACHE_WINDOW_DAYS = 8 * 7 + 6


def cache_window(today: date, lookback_weeks: int) -> tuple[date, date]:
    """Monday of the current week minus lookback_weeks, through today."""
    start_of_week = today - timedelta(days=today.weekday())
    start = start_of_week - timedelta(weeks=lookback_weeks)
    return start, today


def write_date_ok(
    d: date,
    today: date,
    lookback_weeks: int,
    max_span_days: int = 14,
) -> bool:
    window_start, _ = cache_window(today, lookback_weeks)
    return window_start <= d <= today + timedelta(days=max_span_days)


def plan_span_ok(start: date, end: date, max_span_days: int = 14) -> bool:
    if start > end:
        return False
    return (end - start).days <= max_span_days
