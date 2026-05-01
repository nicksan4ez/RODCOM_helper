from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET


NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}


@dataclass(frozen=True)
class SheetData:
    name: str
    rows: list[list[str]]


def read_xlsx_sheets(path: str | Path) -> list[SheetData]:
    workbook_path = Path(path)
    with zipfile.ZipFile(workbook_path) as archive:
        shared_strings = _read_shared_strings(archive)
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        rid_to_target = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels}

        sheets = []
        for sheet in workbook.findall(".//main:sheet", NS):
            name = sheet.attrib["name"].strip()
            rid = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
            target = rid_to_target[rid]
            sheet_path = "xl/" + target.lstrip("/")
            rows = _read_sheet_rows(archive, sheet_path, shared_strings)
            sheets.append(SheetData(name=name, rows=rows))
        return sheets


def _read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return [
        "".join(text.text or "" for text in item.findall(".//main:t", NS))
        for item in root.findall("main:si", NS)
    ]


def _read_sheet_rows(
    archive: zipfile.ZipFile,
    sheet_path: str,
    shared_strings: list[str],
) -> list[list[str]]:
    root = ET.fromstring(archive.read(sheet_path))
    rows = []
    for row in root.findall(".//main:sheetData/main:row", NS):
        values: list[str] = []
        previous_column = 0
        for cell in row.findall("main:c", NS):
            column = _column_number(cell.attrib.get("r", "A"))
            while previous_column + 1 < column:
                values.append("")
                previous_column += 1
            values.append(_cell_text(cell, shared_strings))
            previous_column = column
        rows.append(values)
    return rows


def _cell_text(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    value = cell.find("main:v", NS)
    inline = cell.find("main:is", NS)
    if cell_type == "s" and value is not None:
        return shared_strings[int(value.text or "0")]
    if cell_type == "inlineStr" and inline is not None:
        return "".join(text.text or "" for text in inline.findall(".//main:t", NS))
    return (value.text if value is not None else "") or ""


def _column_number(ref: str) -> int:
    letters = "".join(char for char in ref if char.isalpha())
    number = 0
    for char in letters:
        number = number * 26 + ord(char.upper()) - 64
    return number

