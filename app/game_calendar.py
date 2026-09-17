"""Calendar arithmetic + display formatting for the Reminders window (app/windows/reminders.py).

Weekday convention, confirmed live: a continuous 7-day cycle across the whole save (not reset each
season, and every season is a fixed 30 days) -- Year 1 Spring 1 is a Sunday, and Year 2 Spring 1
(day_index 120) correctly lands on a Monday under this formula (120 % 7 == 1), confirming the
continuous-cycle assumption rather than a per-season reset. Not cross-checked against anything in
pointer_map.md (no live weekday value is read anywhere), but the anchor itself is real, not a
guess.
"""
from __future__ import annotations

SEASON_ORDER = ["Spring", "Summer", "Fall", "Winter"]
DAYS_PER_SEASON = 30
DAYS_PER_YEAR = len(SEASON_ORDER) * DAYS_PER_SEASON
WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


def day_index(year: int, season: str, day: int) -> int:
    """0-based total day count since Year 1 Spring 1 (which is index 0)."""
    return (year - 1) * DAYS_PER_YEAR + SEASON_ORDER.index(season) * DAYS_PER_SEASON + (day - 1)


def add_days(year: int, season: str, day: int, offset: int) -> tuple[int, str, int]:
    total = day_index(year, season, day) + offset
    year = total // DAYS_PER_YEAR + 1
    remainder = total % DAYS_PER_YEAR
    season = SEASON_ORDER[remainder // DAYS_PER_SEASON]
    day = remainder % DAYS_PER_SEASON + 1
    return year, season, day


def weekday_name(year: int, season: str, day: int) -> str:
    return WEEKDAYS[day_index(year, season, day) % 7]


def format_time_12h(hour: int, minute: int) -> str:
    h12 = hour % 12 or 12
    ampm = "AM" if hour < 12 else "PM"
    return f"{h12}:{minute:02d} {ampm}"


def format_date(year: int, season: str, day: int) -> str:
    # Display format: "Winter 17 Yr 1, Monday"
    return f"{season} {day} Yr {year}, {weekday_name(year, season, day)}"


def format_datetime(year: int, season: str, day: int, hour: int, minute: int) -> str:
    return f"{format_date(year, season, day)} - {format_time_12h(hour, minute)}"
