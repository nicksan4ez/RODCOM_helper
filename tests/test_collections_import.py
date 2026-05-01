from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from rodcom_bot.collections_import import parse_collections_from_xlsx, import_collections_from_xlsx
from rodcom_bot.db import Database
from rodcom_bot.docx_import import import_people_from_docx


ROOT = Path(__file__).resolve().parents[1]


class CollectionsImportTest(unittest.TestCase):
    def test_parse_current_cashbox_workbook(self):
        collections = parse_collections_from_xlsx(ROOT / "Касса класса.xlsx")
        titles = {collection.title for collection in collections}

        self.assertIn("Прописи", titles)
        self.assertIn("Дельфин", titles)
        propisi = next(collection for collection in collections if collection.title == "Прописи")
        self.assertEqual(propisi.expected_amount, 1163)
        self.assertEqual(len(propisi.members), 36)

    def test_parse_can_include_only_selected_active_sheets(self):
        collections = parse_collections_from_xlsx(
            ROOT / "Касса класса.xlsx",
            include_titles={"Последний звонок значки"},
        )

        self.assertEqual([collection.title for collection in collections], ["Последний звонок значки"])
        self.assertEqual(collections[0].expected_amount, 220)

    def test_household_sheets_are_not_imported_as_simple_collections(self):
        collections = parse_collections_from_xlsx(
            ROOT / "Касса класса.xlsx",
            include_titles={"Хоз нужды 2025-2026", "Хоз нужды 2026-2027"},
        )

        self.assertEqual(collections, [])

    def test_import_is_idempotent(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "test.sqlite3")
            db.migrate()
            db.seed_people(import_people_from_docx(ROOT / "List.docx"))

            first = import_collections_from_xlsx(db, ROOT / "Касса класса.xlsx")
            second = import_collections_from_xlsx(db, ROOT / "Касса класса.xlsx")

            self.assertEqual(len(first), len(second))
            self.assertEqual(len(db.list_collections()), len(first))


if __name__ == "__main__":
    unittest.main()
