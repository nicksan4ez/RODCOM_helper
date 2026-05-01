from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zipfile

from rodcom_bot.db import Database
from rodcom_bot.report_xlsx import build_collections_report


class ReportXlsxTest(unittest.TestCase):
    def test_build_collections_report_from_database(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "test.sqlite3")
            db.migrate()
            anna_id = db.add_person("Иванова Анна", "child", 5, 10, 2018, "")
            db.add_person("Петров Борис", "child", 6, 11, 2018, "")

            collection_id = db.create_collection_for_active_children("Последний звонок значки", 220)
            db.set_collection_payment(collection_id, anna_id, 220)

            output = build_collections_report(db, Path(temp_dir) / "report.xlsx")

            self.assertTrue(output.exists())
            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())
                self.assertIn("xl/workbook.xml", names)
                self.assertIn("xl/worksheets/sheet1.xml", names)
                self.assertIn("xl/worksheets/sheet2.xml", names)
                self.assertIn("xl/worksheets/sheet3.xml", names)

                workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")
                summary_xml = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
                debtors_xml = archive.read("xl/worksheets/sheet2.xml").decode("utf-8")
                collection_xml = archive.read("xl/worksheets/sheet3.xml").decode("utf-8")

            self.assertIn("Сводка", workbook_xml)
            self.assertIn("Должники", workbook_xml)
            self.assertIn("Последний звонок значки", workbook_xml)
            self.assertIn("Сборы родкома", summary_xml)
            self.assertIn("Петров Борис", debtors_xml)
            self.assertIn("Иванова Анна", collection_xml)
            self.assertIn("Сдал", collection_xml)


if __name__ == "__main__":
    unittest.main()
