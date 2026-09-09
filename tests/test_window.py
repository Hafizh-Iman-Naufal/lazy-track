from datetime import date, timedelta

from lazytrack.domain.window import cache_window, plan_span_ok, write_date_ok


class TestCacheWindow:
    def test_sunday_is_current_week_monday_minus_lookback(self):
        today = date(2026, 9, 6)  # Sunday
        start, end = cache_window(today, 4)
        assert start == date(2026, 8, 3)  # Mon 31 Aug minus 4 weeks
        assert end == today

    def test_monday_starts_this_week(self):
        today = date(2026, 8, 31)
        start, end = cache_window(today, 4)
        assert start == date(2026, 8, 3)
        assert end == today


class TestWriteDateOk:
    def test_inside_window(self):
        today = date(2026, 9, 6)
        assert write_date_ok(date(2026, 9, 4), today, 4, 14) is True

    def test_before_window_rejected(self):
        today = date(2026, 9, 6)
        assert write_date_ok(date(2025, 1, 1), today, 4, 14) is False

    def test_future_within_span(self):
        today = date(2026, 9, 6)
        assert write_date_ok(today + timedelta(days=14), today, 4, 14) is True
        assert write_date_ok(today + timedelta(days=15), today, 4, 14) is False


class TestPlanSpanOk:
    def test_span(self):
        assert plan_span_ok(date(2026, 9, 1), date(2026, 9, 14), 14) is True
        assert plan_span_ok(date(2026, 9, 1), date(2026, 9, 16), 14) is False
        assert plan_span_ok(date(2026, 9, 5), date(2026, 9, 1), 14) is False
