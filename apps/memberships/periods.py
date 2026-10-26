"""
Membership period arithmetic.

Convention (inclusive periods):
- A period of N **days** starting on S ends on S + N - 1.
- A period of N **months** starting on S ends the day before the same day of
  the month N months later. If that day doesn't exist in the target month
  (e.g. the 31st, or 29 February in a non-leap year), the period runs to the
  last day of the target month.

Examples: 10 Jan + 1 month -> 9 Feb; 1 Mar + 1 month -> 31 Mar;
31 Jan 2025 + 1 month -> 28 Feb 2025; 31 Jan 2024 + 1 month -> 29 Feb 2024;
29 Feb 2024 + 12 months -> 28 Feb 2025.

Consecutive periods chain without gaps: the next period starts on end + 1.
"""
import calendar
from datetime import timedelta

DAY = "day"
MONTH = "month"


def _shift_months(year, month, months):
    index = year * 12 + (month - 1) + months
    return index // 12, index % 12 + 1


def next_period_start(start, value, unit):
    """First day *after* a period of ``value`` ``unit`` beginning on ``start``."""
    if value < 1:
        raise ValueError("Duration must be at least 1.")
    if unit == DAY:
        return start + timedelta(days=value)
    if unit == MONTH:
        year, month = _shift_months(start.year, start.month, value)
        last_day = calendar.monthrange(year, month)[1]
        if start.day <= last_day:
            return start.replace(year=year, month=month, day=start.day)
        # Day doesn't exist in the target month: period covers that whole month.
        year, month = _shift_months(year, month, 1)
        return start.replace(year=year, month=month, day=1)
    raise ValueError(f"Unknown duration unit: {unit}")


def period_end(start, value, unit):
    """Last day (inclusive) of a period of ``value`` ``unit`` beginning on ``start``."""
    return next_period_start(start, value, unit) - timedelta(days=1)


def describe(value, unit):
    if unit == MONTH:
        if value % 12 == 0:
            years = value // 12
            return "1 year" if years == 1 else f"{years} years"
        return "1 month" if value == 1 else f"{value} months"
    if value % 7 == 0 and value >= 7:
        weeks = value // 7
        return "1 week" if weeks == 1 else f"{weeks} weeks"
    return "1 day" if value == 1 else f"{value} days"
