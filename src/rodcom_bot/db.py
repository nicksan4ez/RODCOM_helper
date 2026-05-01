from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .docx_import import ImportedPerson


@dataclass(frozen=True)
class Person:
    id: int
    full_name: str
    role: str
    birth_month: int
    birth_day: int
    birth_year: int | None
    note: str
    active: bool


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")

    def close(self) -> None:
        self.connection.close()

    def migrate(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS people (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('child', 'teacher')),
                birth_month INTEGER NOT NULL CHECK (birth_month BETWEEN 1 AND 12),
                birth_day INTEGER NOT NULL CHECK (birth_day BETWEEN 1 AND 31),
                birth_year INTEGER,
                note TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                UNIQUE(full_name, role)
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sent_reminders (
                person_id INTEGER NOT NULL REFERENCES people(id),
                birthday_date TEXT NOT NULL,
                reminder_date TEXT NOT NULL,
                sent_at TEXT NOT NULL,
                PRIMARY KEY(person_id, birthday_date, reminder_date)
            );

            CREATE TABLE IF NOT EXISTS user_states (
                chat_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                state TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL,
                PRIMARY KEY(chat_id, user_id)
            );
            """
        )
        self.connection.commit()

    def seed_people(self, people: list[ImportedPerson]) -> int:
        inserted = 0
        for person in people:
            cursor = self.connection.execute(
                """
                INSERT OR IGNORE INTO people
                    (full_name, role, birth_month, birth_day, birth_year, note, active)
                VALUES (?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    person.full_name,
                    person.role,
                    person.birth_month,
                    person.birth_day,
                    person.birth_year,
                    person.note,
                ),
            )
            inserted += cursor.rowcount
        self.connection.commit()
        return inserted

    def list_people(self, active_only: bool = True) -> list[Person]:
        query = "SELECT * FROM people"
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY role DESC, birth_month, birth_day, full_name"
        return [self._row_to_person(row) for row in self.connection.execute(query)]

    def add_person(
        self,
        full_name: str,
        role: str,
        birth_month: int,
        birth_day: int,
        birth_year: int | None = None,
        note: str = "",
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO people
                (full_name, role, birth_month, birth_day, birth_year, note, active)
            VALUES (?, ?, ?, ?, ?, ?, 1)
            """,
            (full_name, role, birth_month, birth_day, birth_year, note),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def update_person_note(self, person_id: int, note: str) -> bool:
        cursor = self.connection.execute(
            "UPDATE people SET note = ? WHERE id = ?",
            (note, person_id),
        )
        self.connection.commit()
        return cursor.rowcount > 0

    def update_person_field(self, person_id: int, field: str, value: object) -> bool:
        allowed_fields = {"full_name", "role", "birth_month", "birth_day", "birth_year", "note"}
        if field not in allowed_fields:
            raise ValueError(f"Unsupported people field: {field}")
        cursor = self.connection.execute(
            f"UPDATE people SET {field} = ? WHERE id = ?",
            (value, person_id),
        )
        self.connection.commit()
        return cursor.rowcount > 0

    def disable_person(self, person_id: int) -> bool:
        cursor = self.connection.execute(
            "UPDATE people SET active = 0 WHERE id = ?",
            (person_id,),
        )
        self.connection.commit()
        return cursor.rowcount > 0

    def restore_person(self, person_id: int) -> bool:
        cursor = self.connection.execute(
            "UPDATE people SET active = 1 WHERE id = ?",
            (person_id,),
        )
        self.connection.commit()
        return cursor.rowcount > 0

    def delete_person(self, person_id: int) -> bool:
        self.connection.execute("DELETE FROM sent_reminders WHERE person_id = ?", (person_id,))
        cursor = self.connection.execute("DELETE FROM people WHERE id = ?", (person_id,))
        self.connection.commit()
        return cursor.rowcount > 0

    def mark_sent(self, person_id: int, birthday: date, reminder: date) -> None:
        self.connection.execute(
            """
            INSERT OR IGNORE INTO sent_reminders
                (person_id, birthday_date, reminder_date, sent_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                person_id,
                birthday.isoformat(),
                reminder.isoformat(),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        self.connection.commit()

    def was_sent(self, person_id: int, birthday: date, reminder: date) -> bool:
        row = self.connection.execute(
            """
            SELECT 1 FROM sent_reminders
            WHERE person_id = ? AND birthday_date = ? AND reminder_date = ?
            """,
            (person_id, birthday.isoformat(), reminder.isoformat()),
        ).fetchone()
        return row is not None

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM settings WHERE key = ?",
            (key,),
        ).fetchone()
        return row["value"] if row else default

    def get_user_state(self, chat_id: int | str, user_id: int | str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT state, payload FROM user_states WHERE chat_id = ? AND user_id = ?",
            (str(chat_id), str(user_id)),
        ).fetchone()

    def set_user_state(
        self,
        chat_id: int | str,
        user_id: int | str,
        state: str,
        payload: str = "{}",
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO user_states(chat_id, user_id, state, payload, updated_at)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET
                state = excluded.state,
                payload = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (str(chat_id), str(user_id), state, payload, datetime.now().isoformat(timespec="seconds")),
        )
        self.connection.commit()

    def clear_user_state(self, chat_id: int | str, user_id: int | str) -> None:
        self.connection.execute(
            "DELETE FROM user_states WHERE chat_id = ? AND user_id = ?",
            (str(chat_id), str(user_id)),
        )
        self.connection.commit()

    def set_setting(self, key: str, value: str) -> None:
        self.connection.execute(
            """
            INSERT INTO settings(key, value) VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        self.connection.commit()

    @staticmethod
    def _row_to_person(row: sqlite3.Row) -> Person:
        return Person(
            id=row["id"],
            full_name=row["full_name"],
            role=row["role"],
            birth_month=row["birth_month"],
            birth_day=row["birth_day"],
            birth_year=row["birth_year"],
            note=row["note"],
            active=bool(row["active"]),
        )
