from datetime import date, datetime
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from rodcom_bot.bot import RodcomBot
from rodcom_bot.config import Config


class FakeTelegram:
    def __init__(self, failing_recipients=None):
        self.failing_recipients = {str(value) for value in failing_recipients or []}
        self.messages = []

    def send_message(self, chat_id, text, reply_markup=None):
        if str(chat_id) in self.failing_recipients:
            raise RuntimeError("send failed")
        self.messages.append((str(chat_id), text))


class BotReminderDeliveryTest(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)

    def tearDown(self):
        logging.disable(logging.NOTSET)

    def test_due_reminders_go_to_admin_chat_and_admin_users(self):
        with TemporaryDirectory() as temp_dir:
            bot = RodcomBot(_config(Path(temp_dir) / "test.sqlite3"))
            bot.telegram = FakeTelegram()
            person_id = bot.db.add_person("Шакун Роман", "child", 5, 30, 2018, "")

            bot.send_due_reminders(date(2026, 5, 29))

            recipients = [chat_id for chat_id, _ in bot.telegram.messages]
            self.assertEqual(recipients, ["-1003394224633", "111", "222"])
            self.assertTrue(bot.db.was_sent(person_id, date(2026, 5, 30), date(2026, 5, 29)))

    def test_one_failed_recipient_does_not_block_successful_recipients(self):
        with TemporaryDirectory() as temp_dir:
            bot = RodcomBot(_config(Path(temp_dir) / "test.sqlite3"))
            bot.telegram = FakeTelegram(failing_recipients={"111"})
            person_id = bot.db.add_person("Шакун Роман", "child", 5, 30, 2018, "")

            bot.send_due_reminders(date(2026, 5, 29))

            recipients = [chat_id for chat_id, _ in bot.telegram.messages]
            self.assertEqual(recipients, ["-1003394224633", "222"])
            self.assertTrue(bot.db.was_sent(person_id, date(2026, 5, 30), date(2026, 5, 29)))

    def test_all_failed_recipients_keep_reminder_unsent_for_retry(self):
        with TemporaryDirectory() as temp_dir:
            bot = RodcomBot(_config(Path(temp_dir) / "test.sqlite3"))
            bot.telegram = FakeTelegram(failing_recipients={"-1003394224633", "111", "222"})
            person_id = bot.db.add_person("Шакун Роман", "child", 5, 30, 2018, "")

            with self.assertRaises(RuntimeError):
                bot.send_due_reminders(date(2026, 5, 29))

            self.assertFalse(bot.db.was_sent(person_id, date(2026, 5, 30), date(2026, 5, 29)))

    def test_test_delivery_reports_successes_and_failures(self):
        with TemporaryDirectory() as temp_dir:
            bot = RodcomBot(_config(Path(temp_dir) / "test.sqlite3"))
            bot.telegram = FakeTelegram(failing_recipients={"111"})

            result = bot._test_delivery()

            recipients = [chat_id for chat_id, _ in bot.telegram.messages]
            self.assertEqual(recipients, ["-1003394224633", "222"])
            self.assertIn("-1003394224633", result)
            self.assertIn("111", result)
            self.assertIn("222", result)
            self.assertIn("Итого: <b>2/3</b>", result)

    def test_daily_check_runs_after_configured_time_once_per_day(self):
        with TemporaryDirectory() as temp_dir:
            bot = RodcomBot(_config(Path(temp_dir) / "test.sqlite3"))

            self.assertFalse(bot._should_run_daily_check(datetime(2026, 5, 29, 7, 29), None))
            self.assertTrue(bot._should_run_daily_check(datetime(2026, 5, 29, 7, 30), None))
            self.assertTrue(bot._should_run_daily_check(datetime(2026, 5, 29, 8, 15), None))
            self.assertFalse(bot._should_run_daily_check(datetime(2026, 5, 29, 8, 15), "2026-05-29"))


def _config(database_path: Path) -> Config:
    return Config(
        bot_token="test-token",
        admin_chat_id="-1003394224633",
        admin_user_ids={222, 111},
        timezone="Asia/Vladivostok",
        check_time="07:30",
        database_path=database_path,
        source_docx_path=None,
        yandex_disk_token=None,
        yandex_disk_report_path=None,
    )


if __name__ == "__main__":
    unittest.main()
