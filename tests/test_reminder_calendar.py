from datetime import date
import unittest

from rodcom_bot.calendar_ru import WorkCalendar, celebration_date, reminder_date


class ReminderCalendarTest(unittest.TestCase):
    def setUp(self):
        self.calendar = WorkCalendar.default()

    def test_workday_birthday_reminder_two_days_before(self):
        birthday = date(2026, 9, 14)  # Monday

        self.assertEqual(celebration_date(birthday, self.calendar), date(2026, 9, 14))
        self.assertEqual(reminder_date(birthday, self.calendar), date(2026, 9, 11))

    def test_saturday_birthday_is_celebrated_on_friday(self):
        birthday = date(2026, 5, 30)  # Saturday

        self.assertEqual(celebration_date(birthday, self.calendar), date(2026, 5, 29))
        self.assertEqual(reminder_date(birthday, self.calendar), date(2026, 5, 27))

    def test_sunday_birthday_is_celebrated_on_monday(self):
        birthday = date(2026, 6, 7)  # Sunday

        self.assertEqual(celebration_date(birthday, self.calendar), date(2026, 6, 8))
        self.assertEqual(reminder_date(birthday, self.calendar), date(2026, 6, 5))

    def test_holiday_birthday_uses_nearest_workday(self):
        birthday = date(2026, 5, 9)  # Victory Day, Saturday

        self.assertEqual(celebration_date(birthday, self.calendar), date(2026, 5, 8))
        self.assertLess(reminder_date(birthday, self.calendar), celebration_date(birthday, self.calendar))

    def test_long_new_year_holidays(self):
        birthday = date(2026, 1, 7)

        self.assertEqual(celebration_date(birthday, self.calendar), date(2026, 1, 12))
        self.assertEqual(reminder_date(birthday, self.calendar), date(2025, 12, 31))

    def test_year_boundary(self):
        birthday = date(2026, 12, 31)

        self.assertEqual(celebration_date(birthday, self.calendar), date(2026, 12, 30))
        self.assertLess(reminder_date(birthday, self.calendar), celebration_date(birthday, self.calendar))


if __name__ == "__main__":
    unittest.main()
