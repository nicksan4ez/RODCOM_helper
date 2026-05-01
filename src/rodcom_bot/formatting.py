from __future__ import annotations

from datetime import date


MONTHS_GENITIVE = {
    1: "января",
    2: "февраля",
    3: "марта",
    4: "апреля",
    5: "мая",
    6: "июня",
    7: "июля",
    8: "августа",
    9: "сентября",
    10: "октября",
    11: "ноября",
    12: "декабря",
}


WEEKDAYS = {
    0: "понедельник",
    1: "вторник",
    2: "среда",
    3: "четверг",
    4: "пятница",
    5: "суббота",
    6: "воскресенье",
}


def format_date_ru(day: date) -> str:
    return f"{day.day} {MONTHS_GENITIVE[day.month]} {day.year}, {WEEKDAYS[day.weekday()]}"


def age_on(birth_year: int | None, day: date) -> int | None:
    if birth_year is None:
        return None
    return day.year - birth_year

