from __future__ import annotations

import sqlite3
import threading
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


@dataclass(frozen=True)
class Collection:
    id: int
    title: str
    expected_amount: int
    status: str
    source: str
    created_at: str


@dataclass(frozen=True)
class CollectionMember:
    collection_id: int
    person_id: int
    full_name: str
    expected_amount: int
    paid_amount: int
    status: str
    comment: str


@dataclass(frozen=True)
class CollectionSummary:
    collection: Collection
    members_count: int
    paid_count: int
    expected_total: int
    paid_total: int


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")

    def close(self) -> None:
        with self._lock:
            self.connection.close()

    def migrate(self) -> None:
        with self._lock:
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

                CREATE TABLE IF NOT EXISTS collections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL UNIQUE,
                    expected_amount INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL CHECK (status IN ('active', 'closed')) DEFAULT 'active',
                    source TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS collection_members (
                    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
                    person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
                    expected_amount INTEGER NOT NULL DEFAULT 0,
                    paid_amount INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL CHECK (status IN ('unpaid', 'partial', 'paid')) DEFAULT 'unpaid',
                    comment TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(collection_id, person_id)
                );
                """
            )
            self.connection.commit()

    def seed_people(self, people: list[ImportedPerson]) -> int:
        with self._lock:
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
        with self._lock:
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
        with self._lock:
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
        with self._lock:
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
        with self._lock:
            cursor = self.connection.execute(
                f"UPDATE people SET {field} = ? WHERE id = ?",
                (value, person_id),
            )
            self.connection.commit()
            return cursor.rowcount > 0

    def disable_person(self, person_id: int) -> bool:
        with self._lock:
            cursor = self.connection.execute(
                "UPDATE people SET active = 0 WHERE id = ?",
                (person_id,),
            )
            self.connection.commit()
            return cursor.rowcount > 0

    def restore_person(self, person_id: int) -> bool:
        with self._lock:
            cursor = self.connection.execute(
                "UPDATE people SET active = 1 WHERE id = ?",
                (person_id,),
            )
            self.connection.commit()
            return cursor.rowcount > 0

    def delete_person(self, person_id: int) -> bool:
        with self._lock:
            self.connection.execute("DELETE FROM sent_reminders WHERE person_id = ?", (person_id,))
            cursor = self.connection.execute("DELETE FROM people WHERE id = ?", (person_id,))
            self.connection.commit()
            return cursor.rowcount > 0

    def mark_sent(self, person_id: int, birthday: date, reminder: date) -> None:
        with self._lock:
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
        with self._lock:
            row = self.connection.execute(
                """
                SELECT 1 FROM sent_reminders
                WHERE person_id = ? AND birthday_date = ? AND reminder_date = ?
                """,
                (person_id, birthday.isoformat(), reminder.isoformat()),
            ).fetchone()
            return row is not None

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self.connection.execute(
                "SELECT value FROM settings WHERE key = ?",
                (key,),
            ).fetchone()
            return row["value"] if row else default

    def get_user_state(self, chat_id: int | str, user_id: int | str) -> sqlite3.Row | None:
        with self._lock:
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
        with self._lock:
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
        with self._lock:
            self.connection.execute(
                "DELETE FROM user_states WHERE chat_id = ? AND user_id = ?",
                (str(chat_id), str(user_id)),
            )
            self.connection.commit()

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self.connection.execute(
                """
                INSERT INTO settings(key, value) VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
            self.connection.commit()

    def find_person_id_by_name(self, full_name: str) -> int | None:
        normalized = _normalize_name(full_name)
        with self._lock:
            for row in self.connection.execute("SELECT id, full_name FROM people"):
                if _normalize_name(row["full_name"]) == normalized:
                    return int(row["id"])
        return None

    def upsert_collection(self, title: str, expected_amount: int, status: str, source: str) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock:
            self.connection.execute(
                """
                INSERT INTO collections(title, expected_amount, status, source, created_at)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(title) DO UPDATE SET
                    expected_amount = excluded.expected_amount,
                    status = excluded.status,
                    source = excluded.source
                """,
                (title, expected_amount, status, source, now),
            )
            row = self.connection.execute(
                "SELECT id FROM collections WHERE title = ?",
                (title,),
            ).fetchone()
            self.connection.commit()
            return int(row["id"])

    def upsert_collection_member(
        self,
        collection_id: int,
        person_id: int,
        expected_amount: int,
        paid_amount: int,
        comment: str = "",
    ) -> None:
        status = _payment_status(expected_amount, paid_amount)
        with self._lock:
            self.connection.execute(
                """
                INSERT INTO collection_members(
                    collection_id, person_id, expected_amount, paid_amount, status, comment
                )
                VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(collection_id, person_id) DO UPDATE SET
                    expected_amount = excluded.expected_amount,
                    paid_amount = excluded.paid_amount,
                    status = excluded.status,
                    comment = excluded.comment
                """,
                (collection_id, person_id, expected_amount, paid_amount, status, comment),
            )
            self.connection.commit()

    def list_collections(self) -> list[Collection]:
        with self._lock:
            return [
                Collection(
                    id=row["id"],
                    title=row["title"],
                    expected_amount=row["expected_amount"],
                    status=row["status"],
                    source=row["source"],
                    created_at=row["created_at"],
                )
                for row in self.connection.execute("SELECT * FROM collections ORDER BY status, title")
            ]

    def create_collection_for_active_children(self, title: str, expected_amount: int) -> int:
        collection_id = self.upsert_collection(
            title=title,
            expected_amount=expected_amount,
            status="active",
            source="bot",
        )
        with self._lock:
            child_rows = self.connection.execute(
                "SELECT id FROM people WHERE active = 1 AND role = 'child'"
            ).fetchall()
            for row in child_rows:
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO collection_members(
                        collection_id, person_id, expected_amount, paid_amount, status, comment
                    )
                    VALUES(?, ?, ?, 0, 'unpaid', '')
                    """,
                    (collection_id, int(row["id"]), expected_amount),
                )
            self.connection.commit()
        return collection_id

    def list_collection_summaries(self, active_only: bool = True) -> list[CollectionSummary]:
        with self._lock:
            query = """
                SELECT
                    c.*,
                    COUNT(cm.person_id) AS members_count,
                    SUM(CASE WHEN cm.status = 'paid' THEN 1 ELSE 0 END) AS paid_count,
                    SUM(cm.expected_amount) AS expected_total,
                    SUM(cm.paid_amount) AS paid_total
                FROM collections c
                LEFT JOIN collection_members cm ON cm.collection_id = c.id
            """
            params: tuple[object, ...] = ()
            if active_only:
                query += " WHERE c.status = ?"
                params = ("active",)
            query += " GROUP BY c.id ORDER BY c.status, c.title"
            summaries = []
            for row in self.connection.execute(query, params):
                collection = Collection(
                    id=row["id"],
                    title=row["title"],
                    expected_amount=row["expected_amount"],
                    status=row["status"],
                    source=row["source"],
                    created_at=row["created_at"],
                )
                summaries.append(
                    CollectionSummary(
                        collection=collection,
                        members_count=int(row["members_count"] or 0),
                        paid_count=int(row["paid_count"] or 0),
                        expected_total=int(row["expected_total"] or 0),
                        paid_total=int(row["paid_total"] or 0),
                    )
                )
            return summaries

    def set_collection_payment(self, collection_id: int, person_id: int, paid_amount: int) -> bool:
        with self._lock:
            row = self.connection.execute(
                """
                SELECT expected_amount FROM collection_members
                WHERE collection_id = ? AND person_id = ?
                """,
                (collection_id, person_id),
            ).fetchone()
            if row is None:
                return False
            status = _payment_status(int(row["expected_amount"]), paid_amount)
            cursor = self.connection.execute(
                """
                UPDATE collection_members
                SET paid_amount = ?, status = ?
                WHERE collection_id = ? AND person_id = ?
                """,
                (paid_amount, status, collection_id, person_id),
            )
            self.connection.commit()
            return cursor.rowcount > 0

    def get_collection_summary(self, collection_id: int) -> CollectionSummary | None:
        with self._lock:
            query = """
                SELECT
                    c.*,
                    COUNT(cm.person_id) AS members_count,
                    SUM(CASE WHEN cm.status = 'paid' THEN 1 ELSE 0 END) AS paid_count,
                    SUM(cm.expected_amount) AS expected_total,
                    SUM(cm.paid_amount) AS paid_total
                FROM collections c
                LEFT JOIN collection_members cm ON cm.collection_id = c.id
                WHERE c.id = ?
                GROUP BY c.id
            """
            row = self.connection.execute(query, (collection_id,)).fetchone()
            if row is None:
                return None
            return CollectionSummary(
                collection=Collection(
                    id=row["id"],
                    title=row["title"],
                    expected_amount=row["expected_amount"],
                    status=row["status"],
                    source=row["source"],
                    created_at=row["created_at"],
                ),
                members_count=int(row["members_count"] or 0),
                paid_count=int(row["paid_count"] or 0),
                expected_total=int(row["expected_total"] or 0),
                paid_total=int(row["paid_total"] or 0),
            )

    def list_collection_members(self, collection_id: int) -> list[CollectionMember]:
        with self._lock:
            rows = self.connection.execute(
                """
                SELECT cm.*, p.full_name
                FROM collection_members cm
                JOIN people p ON p.id = cm.person_id
                WHERE cm.collection_id = ?
                ORDER BY p.full_name
                """,
                (collection_id,),
            ).fetchall()
            return [
                CollectionMember(
                    collection_id=row["collection_id"],
                    person_id=row["person_id"],
                    full_name=row["full_name"],
                    expected_amount=row["expected_amount"],
                    paid_amount=row["paid_amount"],
                    status=row["status"],
                    comment=row["comment"],
                )
                for row in rows
            ]

    def close_collection(self, collection_id: int) -> bool:
        with self._lock:
            cursor = self.connection.execute(
                "UPDATE collections SET status = 'closed' WHERE id = ?",
                (collection_id,),
            )
            self.connection.commit()
            return cursor.rowcount > 0

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


def _payment_status(expected_amount: int, paid_amount: int) -> str:
    if paid_amount <= 0:
        return "unpaid"
    if expected_amount <= 0 or paid_amount >= expected_amount:
        return "paid"
    return "partial"


def _normalize_name(value: str) -> str:
    return " ".join(value.replace("\u00a0", " ").split()).casefold()
