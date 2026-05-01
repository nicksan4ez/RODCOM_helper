from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from xml.etree import ElementTree as ET


MONTHS_RU = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}


NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


@dataclass(frozen=True)
class ImportedPerson:
    full_name: str
    role: str
    birth_month: int
    birth_day: int
    birth_year: int | None = None
    note: str = ""


def import_people_from_docx(path: str | Path) -> list[ImportedPerson]:
    document_xml = _read_document_xml(Path(path))
    root = ET.fromstring(document_xml)
    table_people = _people_from_first_table(root)
    teacher = _teacher_from_paragraphs(root)
    if teacher:
        table_people.append(teacher)
    return table_people


def _read_document_xml(path: Path) -> bytes:
    with zipfile.ZipFile(path) as archive:
        return archive.read("word/document.xml")


def _people_from_first_table(root: ET.Element) -> list[ImportedPerson]:
    tables = root.findall(".//w:tbl", NS)
    if not tables:
        return []

    people: list[ImportedPerson] = []
    rows = tables[0].findall("./w:tr", NS)
    for row in rows[1:]:
        cells = [_cell_text(cell).strip() for cell in row.findall("./w:tc", NS)]
        if len(cells) < 3 or not cells[0].isdigit():
            continue
        parsed_date = _parse_ru_date(cells[2])
        if not parsed_date:
            continue
        people.append(
            ImportedPerson(
                full_name=_normalize_spaces(cells[1]),
                role="child",
                birth_month=parsed_date[0],
                birth_day=parsed_date[1],
                birth_year=parsed_date[2],
                note=_normalize_spaces(cells[3]) if len(cells) > 3 else "",
            )
        )
    return people


def _teacher_from_paragraphs(root: ET.Element) -> ImportedPerson | None:
    for paragraph in root.findall(".//w:p", NS):
        text = _paragraph_text(paragraph).strip()
        if "Швоева Оксана Васильевна" not in text:
            continue
        match = re.search(r"(.+?)\s+[–-]\s+(\d{1,2})\s+([А-Яа-яёЁ]+)", text)
        if not match:
            continue
        month = MONTHS_RU[match.group(3).lower()]
        return ImportedPerson(
            full_name=_normalize_spaces(match.group(1)),
            role="teacher",
            birth_month=month,
            birth_day=int(match.group(2)),
        )
    return None


def _parse_ru_date(value: str) -> tuple[int, int, int | None] | None:
    match = re.search(r"(\d{1,2})\s+([А-Яа-яёЁ]+)(?:\s+(\d{4}))?", value)
    if not match:
        return None
    month_name = match.group(2).lower()
    if month_name not in MONTHS_RU:
        return None
    year = int(match.group(3)) if match.group(3) else None
    return MONTHS_RU[month_name], int(match.group(1)), year


def _cell_text(cell: ET.Element) -> str:
    return "\n".join(
        _paragraph_text(paragraph) for paragraph in cell.findall(".//w:p", NS)
    )


def _paragraph_text(paragraph: ET.Element) -> str:
    return "".join(text.text or "" for text in paragraph.findall(".//w:t", NS))


def _normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\u00a0", " ")).strip()

