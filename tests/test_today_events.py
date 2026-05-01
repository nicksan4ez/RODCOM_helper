from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from rodcom_bot.calendar_ru import WorkCalendar
from rodcom_bot.db import Database
from rodcom_bot.docx_import import ImportedPerson
from rodcom_bot.reminders import ReminderService, format_today_events


class TodayEventsTest(unittest.TestCase):
    def test_today_includes_birthday_celebration_and_reminder_matches(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "test.sqlite3")
            db.migrate()
            db.seed_people(
                [
                    ImportedPerson(
                        full_name="Медведев Вадим",
                        role="child",
                        birth_month=5,
                        birth_day=1,
                        birth_year=2018,
                    )
                ]
            )
            service = ReminderService(db, WorkCalendar.default())

            events = service.matching_today(date(2026, 5, 1))
            text = format_today_events("Сегодня", events, date(2026, 5, 1))

            self.assertEqual(len(events), 1)
            self.assertIn("Медведев Вадим", text)
            self.assertIn("сегодня день рождения", text)


if __name__ == "__main__":
    unittest.main()
