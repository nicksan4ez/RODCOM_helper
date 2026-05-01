from __future__ import annotations

import argparse
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .db import Database
from .xlsx_reader import SheetData, read_xlsx_sheets


SKIP_SHEETS = {
    "список",
    "льготы",
}


@dataclass(frozen=True)
class ImportedCollectionMember:
    full_name: str
    paid_amount: int
    comment: str


@dataclass(frozen=True)
class ImportedCollection:
    title: str
    expected_amount: int
    members: list[ImportedCollectionMember]

    @property
    def status(self) -> str:
        if self.expected_amount <= 0:
            return "active"
        return "active" if any(member.paid_amount < self.expected_amount for member in self.members) else "closed"


def import_collections_from_xlsx(
    db: Database,
    xlsx_path: str | Path,
    include_titles: set[str] | None = None,
) -> list[ImportedCollection]:
    imported = parse_collections_from_xlsx(xlsx_path, include_titles=include_titles)
    for collection in imported:
        collection_id = db.upsert_collection(
            title=collection.title,
            expected_amount=collection.expected_amount,
            status=collection.status,
            source=f"xlsx:{Path(xlsx_path).name}",
        )
        for member in collection.members:
            person_id = db.find_person_id_by_name(member.full_name)
            if person_id is None:
                continue
            db.upsert_collection_member(
                collection_id=collection_id,
                person_id=person_id,
                expected_amount=collection.expected_amount,
                paid_amount=member.paid_amount,
                comment=member.comment,
            )
    return imported


def parse_collections_from_xlsx(
    xlsx_path: str | Path,
    include_titles: set[str] | None = None,
) -> list[ImportedCollection]:
    collections = []
    normalized_include = {_normalize_title(title) for title in include_titles or set()}
    for sheet in read_xlsx_sheets(xlsx_path):
        if normalized_include and _normalize_title(sheet.name) not in normalized_include:
            continue
        parsed = _parse_sheet(sheet)
        if parsed:
            collections.append(parsed)
    return collections


def _parse_sheet(sheet: SheetData) -> ImportedCollection | None:
    if sheet.name.strip().casefold() in SKIP_SHEETS:
        return None

    header_index = _find_header_row(sheet.rows)
    if header_index is None:
        return None

    header = [_clean(value).casefold() for value in sheet.rows[header_index]]
    try:
        name_col = header.index("фио")
    except ValueError:
        return None
    if "сдали" in header:
        return _parse_simple_collection_sheet(sheet, header_index, header, name_col)
    return None


def _parse_simple_collection_sheet(
    sheet: SheetData,
    header_index: int,
    header: list[str],
    name_col: int,
) -> ImportedCollection | None:
    paid_col = header.index("сдали")

    members = []
    paid_values = []
    for row in sheet.rows[header_index + 1 :]:
        if len(row) <= max(name_col, paid_col):
            continue
        number = _clean(row[0]) if row else ""
        full_name = _clean(row[name_col])
        if not number.isdigit() or not full_name:
            continue
        paid_amount = _money_to_int(row[paid_col])
        comment = _comments(row, paid_col + 1)
        members.append(ImportedCollectionMember(full_name=full_name, paid_amount=paid_amount, comment=comment))
        if paid_amount > 0:
            paid_values.append(paid_amount)

    if not members or not paid_values:
        return None

    expected_amount = _expected_amount(paid_values)
    return ImportedCollection(title=sheet.name, expected_amount=expected_amount, members=members)


def _find_header_row(rows: list[list[str]]) -> int | None:
    for index, row in enumerate(rows[:10]):
        normalized = [_clean(value).casefold() for value in row]
        if "фио" in normalized and "сдали" in normalized:
            return index
    return None


def _expected_amount(values: list[int]) -> int:
    counter = Counter(values)
    return counter.most_common(1)[0][0]


def _money_to_int(value: str) -> int:
    cleaned = _clean(value)
    if not cleaned:
        return 0
    match = re.search(r"-?\d+(?:[.,]\d+)?", cleaned)
    if not match:
        return 0
    return int(round(float(match.group(0).replace(",", "."))))


def _comments(row: list[str], start_col: int) -> str:
    values = [_clean(value) for value in row[start_col:] if _clean(value)]
    return "; ".join(values)


def _clean(value: object) -> str:
    return str(value).replace("\u00a0", " ").strip()


def _normalize_title(value: str) -> str:
    return _clean(value).casefold()


def main() -> None:
    parser = argparse.ArgumentParser(description="Import collection data from an XLSX workbook.")
    parser.add_argument("--db", required=True, help="Path to SQLite database.")
    parser.add_argument("--xlsx", required=True, help="Path to source XLSX workbook.")
    parser.add_argument(
        "--include",
        default="",
        help="Comma-separated sheet names to import. Empty means all collection-like sheets.",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Only show detected collections, do not write to the database.",
    )
    args = parser.parse_args()

    include_titles = {title.strip() for title in args.include.split(",") if title.strip()} or None
    if args.preview:
        imported = parse_collections_from_xlsx(args.xlsx, include_titles=include_titles)
        print(f"Detected collections: {len(imported)}")
    else:
        db = Database(args.db)
        db.migrate()
        imported = import_collections_from_xlsx(db, args.xlsx, include_titles=include_titles)
        print(f"Imported collections: {len(imported)}")
    for collection in imported:
        paid_count = sum(
            1
            for member in collection.members
            if collection.expected_amount > 0 and member.paid_amount >= collection.expected_amount
        )
        print(
            f"- {collection.title}: {collection.expected_amount} ₽, "
            f"{paid_count}/{len(collection.members)} paid, {collection.status}"
        )


if __name__ == "__main__":
    main()
