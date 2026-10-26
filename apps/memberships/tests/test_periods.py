from datetime import date

from django.test import SimpleTestCase

from apps.memberships.periods import DAY, MONTH, describe, next_period_start, period_end


class PeriodEndTests(SimpleTestCase):
    def check(self, start, value, unit, expected):
        self.assertEqual(period_end(start, value, unit), expected, f"{start} + {value} {unit}")

    def test_standard_months(self):
        self.check(date(2026, 1, 10), 1, MONTH, date(2026, 2, 9))
        self.check(date(2026, 3, 1), 1, MONTH, date(2026, 3, 31))
        self.check(date(2026, 1, 1), 3, MONTH, date(2026, 3, 31))
        self.check(date(2026, 1, 15), 6, MONTH, date(2026, 7, 14))
        self.check(date(2026, 1, 1), 12, MONTH, date(2026, 12, 31))
        self.check(date(2026, 12, 15), 1, MONTH, date(2027, 1, 14))

    def test_month_end(self):
        self.check(date(2025, 1, 31), 1, MONTH, date(2025, 2, 28))
        self.check(date(2026, 8, 31), 1, MONTH, date(2026, 9, 30))
        self.check(date(2026, 11, 30), 3, MONTH, date(2027, 2, 28))
        self.check(date(2026, 3, 31), 1, MONTH, date(2026, 4, 30))

    def test_leap_years(self):
        self.check(date(2024, 1, 31), 1, MONTH, date(2024, 2, 29))
        self.check(date(2024, 2, 29), 12, MONTH, date(2025, 2, 28))
        self.check(date(2024, 2, 29), 48, MONTH, date(2028, 2, 28))
        self.check(date(2028, 2, 1), 1, MONTH, date(2028, 2, 29))
        self.check(date(2024, 2, 1), 30, DAY, date(2024, 3, 1))

    def test_days(self):
        self.check(date(2026, 1, 1), 1, DAY, date(2026, 1, 1))
        self.check(date(2026, 1, 1), 30, DAY, date(2026, 1, 30))
        self.check(date(2026, 12, 20), 14, DAY, date(2027, 1, 2))

    def test_consecutive_periods_have_no_gaps(self):
        start = date(2025, 1, 31)
        for _ in range(14):
            end = period_end(start, 1, MONTH)
            nxt = next_period_start(start, 1, MONTH)
            self.assertEqual((nxt - end).days, 1)
            self.assertGreaterEqual(end, start)
            start = nxt

    def test_invalid_durations(self):
        with self.assertRaises(ValueError):
            period_end(date(2026, 1, 1), 0, MONTH)
        with self.assertRaises(ValueError):
            period_end(date(2026, 1, 1), 1, "year")

    def test_describe(self):
        self.assertEqual(describe(1, MONTH), "1 month")
        self.assertEqual(describe(3, MONTH), "3 months")
        self.assertEqual(describe(12, MONTH), "1 year")
        self.assertEqual(describe(14, DAY), "2 weeks")
        self.assertEqual(describe(10, DAY), "10 days")
