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

    def test_create_collection_for_active_children(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "test.sqlite3")
            db.migrate()
            child_id = db.add_person("Иванова Анна", "child", 5, 10, 2018, "")
            db.add_person("Учитель Оксана", "teacher", 8, 18, None, "")

            collection_id = db.create_collection_for_active_children("Экскурсия", 1000)
            summaries = db.list_collection_summaries()

            self.assertEqual(len(summaries), 1)
            self.assertEqual(collection_id, summaries[0].collection.id)
            self.assertEqual(summaries[0].members_count, 1)
            self.assertEqual(summaries[0].expected_total, 1000)

            self.assertTrue(db.set_collection_payment(collection_id, child_id, 1000))
            summary = db.list_collection_summaries()[0]
            self.assertEqual(summary.paid_count, 1)
            self.assertEqual(summary.paid_total, 1000)

            self.assertTrue(db.close_collection(collection_id))
            self.assertEqual(db.list_collection_summaries(active_only=True), [])
            self.assertEqual(len(db.list_collection_summaries(active_only=False)), 1)

            self.assertTrue(db.delete_collection(collection_id))
            self.assertEqual(db.list_collection_summaries(active_only=False), [])


if __name__ == "__main__":
    unittest.main()
