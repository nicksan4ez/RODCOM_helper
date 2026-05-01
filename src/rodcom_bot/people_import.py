from __future__ import annotations

import argparse
from pathlib import Path

from .db import Database
from .docx_import import import_people_from_docx


def main() -> None:
    parser = argparse.ArgumentParser(description="Import people from a DOCX file into SQLite.")
    parser.add_argument("--db", required=True, help="Path to SQLite database.")
    parser.add_argument("--docx", required=True, help="Path to source DOCX file.")
    args = parser.parse_args()

    db = Database(args.db)
    db.migrate()
    people = import_people_from_docx(Path(args.docx))
    inserted = db.seed_people(people)
    print(f"Imported people: {inserted} new, {len(people)} found in DOCX")


if __name__ == "__main__":
    main()
