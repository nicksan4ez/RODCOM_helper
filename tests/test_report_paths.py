import unittest

from rodcom_bot.bot import _report_disk_path, _report_folder_path


class ReportPathTest(unittest.TestCase):
    def test_report_path_can_be_file_or_folder(self):
        self.assertEqual(_report_disk_path("/RODCOM/"), "/RODCOM/sbori_report.xlsx")
        self.assertEqual(_report_disk_path("/RODCOM"), "/RODCOM/sbori_report.xlsx")
        self.assertEqual(
            _report_disk_path("/RODCOM/Автоматический_отчет_по_сборам.xlsx"),
            "/RODCOM/Автоматический_отчет_по_сборам.xlsx",
        )

    def test_report_folder_path(self):
        self.assertEqual(_report_folder_path("/RODCOM/sbori_report.xlsx"), "/RODCOM")
        self.assertEqual(_report_folder_path("/sbori_report.xlsx"), "/")


if __name__ == "__main__":
    unittest.main()
