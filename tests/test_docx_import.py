from pathlib import Path
import unittest

from rodcom_bot.docx_import import import_people_from_docx


ROOT = Path(__file__).resolve().parents[1]


class DocxImportTest(unittest.TestCase):
    def test_imports_children_and_teacher(self):
        people = import_people_from_docx(ROOT / "List.docx")

        children = [person for person in people if person.role == "child"]
        teachers = [person for person in people if person.role == "teacher"]

        self.assertEqual(len(children), 36)
        self.assertEqual(len(teachers), 1)
        self.assertTrue(any(person.full_name == "Поспелова Алиса" for person in children))
        self.assertTrue(any(person.note == "Аллергия на мёд" for person in children))
        self.assertEqual(teachers[0].full_name, "Швоева Оксана Васильевна")
        self.assertEqual((teachers[0].birth_day, teachers[0].birth_month), (18, 8))


if __name__ == "__main__":
    unittest.main()
