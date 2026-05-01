from datetime import date
import unittest

from rodcom_bot.db import Person
from rodcom_bot.bot import _parse_time
from rodcom_bot.reminders import ReminderEvent, format_event, format_events


class UiFormattingTest(unittest.TestCase):
    def test_event_escapes_html_in_user_data(self):
        person = Person(
            id=1,
            full_name="Иванов & Петров <test>",
            role="child",
            birth_month=5,
            birth_day=1,
            birth_year=2018,
            note="Аллергия на M&M <важно>",
            active=True,
        )
        event = ReminderEvent(
            person=person,
            birthday=date(2026, 5, 1),
            celebration=date(2026, 4, 30),
            reminder=date(2026, 4, 28),
        )

        text = format_event(event)

        self.assertIn("Иванов &amp; Петров &lt;test&gt;", text)
        self.assertIn("M&amp;M &lt;важно&gt;", text)
        self.assertIn("исполнится 8️⃣", text)
        self.assertIn("🎂", text)

    def test_empty_events_message_is_friendly(self):
        self.assertIn("Нет событий", format_events("Сегодня", []))

    def test_parse_time_normalizes_and_rejects_invalid_values(self):
        self.assertEqual(_parse_time("7:30"), "07:30")
        self.assertEqual(_parse_time("23:59"), "23:59")
        self.assertIsNone(_parse_time("24:00"))
        self.assertIsNone(_parse_time("10:99"))
        self.assertIsNone(_parse_time("утро"))


if __name__ == "__main__":
    unittest.main()
