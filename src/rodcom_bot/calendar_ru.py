from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


FIXED_RU_HOLIDAYS = {
    (1, 1),
    (1, 2),
    (1, 3),
    (1, 4),
    (1, 5),
    (1, 6),
    (1, 7),
    (1, 8),
    (2, 23),
    (3, 8),
    (5, 1),
    (5, 9),
    (6, 12),
    (11, 4),
}


PRODUCTION_OVERRIDES_2026 = {
    # Постановление Правительства РФ от 24.09.2025 N 1466:
    # перенос с 03.01.2026 на 09.01.2026 и с 04.01.2026 на 31.12.2026.
    date(2026, 1, 9): False,
    date(2026, 12, 31): False,
}


@dataclass(frozen=True)
class WorkCalendar:
    """Russian workday calendar with yearly manual overrides."""

    overrides: dict[date, bool]

    @classmethod
    def default(cls) -> "WorkCalendar":
        return cls(overrides=dict(PRODUCTION_OVERRIDES_2026))

    def is_workday(self, day: date) -> bool:
        if day in self.overrides:
            return self.overrides[day]
        if day.weekday() >= 5:
            return False
        return (day.month, day.day) not in FIXED_RU_HOLIDAYS

    def nearest_workday(self, day: date) -> date:
        if self.is_workday(day):
            return day

        for distance in range(1, 15):
            previous_day = day - timedelta(days=distance)
            next_day = day + timedelta(days=distance)
            if self.is_workday(previous_day):
                return previous_day
            if self.is_workday(next_day):
                return next_day
        raise RuntimeError(f"Could not find a nearby workday for {day.isoformat()}")

    def previous_workday_before(self, day: date) -> date:
        candidate = day - timedelta(days=1)
        while not self.is_workday(candidate):
            candidate -= timedelta(days=1)
        return candidate

    def next_workday_on_or_after(self, day: date) -> date:
        candidate = day
        while not self.is_workday(candidate):
            candidate += timedelta(days=1)
        return candidate


def celebration_date(birthday: date, calendar: WorkCalendar) -> date:
    return calendar.next_workday_on_or_after(birthday)


def reminder_date(birthday: date, calendar: WorkCalendar, days_before: int = 1) -> date:
    candidate = birthday - timedelta(days=days_before)

    if not calendar.is_workday(candidate):
        candidate = calendar.previous_workday_before(candidate + timedelta(days=1))

    return candidate


def next_birthday_date(month: int, day: int, today: date) -> date:
    year = today.year
    candidate = _birthday_in_year(year, month, day)
    if candidate < today:
        candidate = _birthday_in_year(year + 1, month, day)
    return candidate


def _birthday_in_year(year: int, month: int, day: int) -> date:
    if month == 2 and day == 29:
        return date(year, 2, 28)
    return date(year, month, day)
