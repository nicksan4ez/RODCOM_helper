from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from rodcom_bot.db import Database


class DbPeopleTest(unittest.TestCase):
    def test_update_disable_restore_and_delete_person(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "test.sqlite3")
            db.migrate()

            person_id = db.add_person("Иванова Анна", "child", 5, 10, 2018, "")

            self.assertTrue(db.update_person_field(person_id, "full_name", "Петрова Анна"))
            self.assertTrue(db.update_person_field(person_id, "birth_day", 6))
            self.assertTrue(db.update_person_field(person_id, "birth_month", 11))
            self.assertTrue(db.update_person_field(person_id, "birth_year", 2019))
            self.assertTrue(db.update_person_field(person_id, "note", "Без шоколада"))

            person = db.list_people()[0]
            self.assertEqual(person.full_name, "Петрова Анна")
            self.assertEqual((person.birth_day, person.birth_month, person.birth_year), (6, 11, 2019))
            self.assertEqual(person.note, "Без шоколада")

            self.assertTrue(db.disable_person(person_id))
            self.assertEqual(db.list_people(), [])
            self.assertEqual(len(db.list_people(active_only=False)), 1)

            self.assertTrue(db.restore_person(person_id))
            self.assertEqual(len(db.list_people()), 1)

            self.assertTrue(db.delete_person(person_id))
            self.assertEqual(db.list_people(active_only=False), [])


if __name__ == "__main__":
    unittest.main()
