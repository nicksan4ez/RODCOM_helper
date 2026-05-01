from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .calendar_ru import WorkCalendar, celebration_date, next_birthday_date, reminder_date
from .db import Database, Person
from .formatting import age_on, format_date_ru
from .ui import h


@dataclass(frozen=True)
class ReminderEvent:
    person: Person
    birthday: date
    celebration: date
    reminder: date


class ReminderService:
    def __init__(self, db: Database, calendar: WorkCalendar):
        self.db = db
        self.calendar = calendar

    def upcoming(self, today: date, limit: int = 10) -> list[ReminderEvent]:
        events = [self._event_for(person, today) for person in self.db.list_people()]
        events.sort(key=lambda event: (event.celebration, event.birthday, event.person.full_name))
        return events[:limit]

    def events_for_month(self, year: int, month: int) -> list[ReminderEvent]:
        result: list[ReminderEvent] = []
        for person in self.db.list_people():
            birthday = date(year, person.birth_month, person.birth_day)
            event = ReminderEvent(
                person=person,
                birthday=birthday,
                celebration=celebration_date(birthday, self.calendar),
                reminder=reminder_date(birthday, self.calendar),
            )
            if event.birthday.month == month:
                result.append(event)
        result.sort(key=lambda event: (event.birthday, event.person.full_name))
        return result

    def due_today(self, today: date) -> list[ReminderEvent]:
        result = []
        for person in self.db.list_people():
            event = self._event_for(person, today)
            if event.reminder == today and not self.db.was_sent(person.id, event.birthday, event.reminder):
                result.append(event)
        result.sort(key=lambda event: (event.celebration, event.person.full_name))
        return result

    def mark_sent(self, events: list[ReminderEvent]) -> None:
        for event in events:
            self.db.mark_sent(event.person.id, event.birthday, event.reminder)

    def _event_for(self, person: Person, today: date) -> ReminderEvent:
        birthday = next_birthday_date(person.birth_month, person.birth_day, today)
        return ReminderEvent(
            person=person,
            birthday=birthday,
            celebration=celebration_date(birthday, self.calendar),
            reminder=reminder_date(birthday, self.calendar),
        )


def format_event(event: ReminderEvent) -> str:
    person = event.person
    role = "👩‍🏫 учитель" if person.role == "teacher" else "🎒 ученик"
    age = age_on(person.birth_year, event.birthday)
    age_text = f" · исполнится {age}" if age is not None else ""
    note = f"\n   📝 {h(person.note)}" if person.note else ""
    return (
        f"🎂 <b>{h(person.full_name)}</b>\n"
        f"   {role}{age_text}\n"
        f"   📅 ДР: {h(format_date_ru(event.birthday))}\n"
        f"   🎁 Поздравить: <b>{h(format_date_ru(event.celebration))}</b>\n"
        f"   🔔 Напомнить: {h(format_date_ru(event.reminder))}"
        f"{note}"
    )


def format_events(title: str, events: list[ReminderEvent]) -> str:
    if not events:
        return f"📭 <b>{h(title)}</b>\n\nНет событий."
    return f"📌 <b>{h(title)}</b>\n\n" + "\n\n".join(format_event(event) for event in events)
