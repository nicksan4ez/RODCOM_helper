from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

from .calendar_ru import WorkCalendar
from .config import Config
from .db import Database
from .docx_import import import_people_from_docx
from .formatting import MONTHS_GENITIVE, format_date_ru
from .reminders import ReminderService, format_events
from .telegram_api import TelegramClient


LOGGER = logging.getLogger(__name__)


class RodcomBot:
    def __init__(self, config: Config):
        self.config = config
        self.db = Database(config.database_path)
        self.db.migrate()
        self.calendar = WorkCalendar.default()
        self.reminders = ReminderService(self.db, self.calendar)
        self.telegram = TelegramClient(config.bot_token)
        self.timezone = ZoneInfo(config.timezone)
        self._stop = threading.Event()

    def bootstrap(self) -> None:
        if not self.db.list_people(active_only=False):
            people = import_people_from_docx(self.config.source_docx_path)
            inserted = self.db.seed_people(people)
            LOGGER.info("Imported %s people from %s", inserted, self.config.source_docx_path)
        self.db.set_setting("timezone", self.config.timezone)
        self.db.set_setting("check_time", self.config.check_time)
        self.db.set_setting("admin_chat_id", self.config.admin_chat_id)

    def run(self) -> None:
        self.bootstrap()
        scheduler = threading.Thread(target=self._scheduler_loop, daemon=True)
        scheduler.start()
        self._poll_loop()

    def _scheduler_loop(self) -> None:
        last_run: str | None = None
        while not self._stop.is_set():
            now = datetime.now(self.timezone)
            today_key = now.date().isoformat()
            if now.strftime("%H:%M") == self.config.check_time and last_run != today_key:
                try:
                    self.send_due_reminders(now.date())
                    last_run = today_key
                except Exception:
                    LOGGER.exception("Daily reminder check failed")
            time.sleep(20)

    def send_due_reminders(self, today: date) -> None:
        events = self.reminders.due_today(today)
        if not events:
            LOGGER.info("No birthday reminders due on %s", today.isoformat())
            return
        message = format_events("Напоминание о днях рождения", events)
        self.telegram.send_message(self.config.admin_chat_id, message)
        self.reminders.mark_sent(events)

    def _poll_loop(self) -> None:
        offset: int | None = None
        while not self._stop.is_set():
            try:
                updates = self.telegram.get_updates(offset=offset)
                for update in updates:
                    offset = int(update["update_id"]) + 1
                    self._handle_update(update)
            except Exception:
                LOGGER.exception("Telegram polling failed")
                time.sleep(5)

    def _handle_update(self, update: dict) -> None:
        if "callback_query" in update:
            self._handle_callback(update["callback_query"])
            return

        message = update.get("message") or {}
        text = (message.get("text") or "").strip()
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        chat_id = chat.get("id")
        user_id = sender.get("id")

        if not text.startswith("/") or chat_id is None:
            if chat_id is not None and user_id is not None:
                state_result = self._handle_state_message(chat_id, user_id, text)
                if state_result:
                    response, keyboard = state_result
                    self.telegram.send_message(chat_id, response, keyboard or main_menu_keyboard())
            return
        if not self._is_authorized(chat_id, user_id):
            self.telegram.send_message(chat_id, "Команда доступна только администраторам родкома.")
            return

        response = self._dispatch(text)
        if response:
            self.telegram.send_message(chat_id, response, main_menu_keyboard())

    def _handle_callback(self, callback: dict) -> None:
        callback_id = callback.get("id")
        data = callback.get("data") or ""
        message = callback.get("message") or {}
        chat = message.get("chat") or {}
        sender = callback.get("from") or {}
        chat_id = chat.get("id")
        user_id = sender.get("id")
        if callback_id:
            self.telegram.answer_callback_query(callback_id)
        if chat_id is None or user_id is None:
            return
        if not self._is_authorized(chat_id, user_id):
            self.telegram.send_message(chat_id, "Кнопки доступны только администраторам родкома.")
            return

        response, keyboard = self._dispatch_callback(chat_id, user_id, data)
        self.telegram.send_message(chat_id, response, keyboard or main_menu_keyboard())

    def _is_authorized(self, chat_id: int, user_id: int | None) -> bool:
        if self.config.admin_user_ids:
            return user_id is not None and user_id in self.config.admin_user_ids
        return str(chat_id) == str(self.config.admin_chat_id)

    def _dispatch(self, text: str) -> str:
        command, _, rest = text.partition(" ")
        command = command.split("@", 1)[0].lower()
        rest = rest.strip()

        today = datetime.now(self.timezone).date()
        if command in {"/start", "/help"}:
            return "Главное меню. Выберите действие кнопкой ниже.\n\n" + HELP_TEXT
        if command == "/next":
            return format_events("Ближайшие дни рождения", self.reminders.upcoming(today, limit=10))
        if command == "/today":
            return format_events("Напоминания на сегодня", self.reminders.due_today(today))
        if command == "/month":
            return self._month(rest, today)
        if command == "/people":
            return self._people()
        if command == "/add":
            return self._add(rest)
        if command == "/edit":
            return self._edit(rest)
        if command == "/disable":
            return self._disable(rest)
        if command == "/restore":
            return self._restore(rest)
        if command == "/delete":
            return self._delete(rest)
        if command == "/settings":
            return self._settings()
        if command == "/test_reminder":
            return format_events("Тестовое напоминание", self.reminders.upcoming(today, limit=3))
        return "Неизвестная команда.\n\n" + HELP_TEXT

    def _dispatch_callback(
        self,
        chat_id: int,
        user_id: int,
        data: str,
    ) -> tuple[str, dict | None]:
        today = datetime.now(self.timezone).date()
        if data == "menu":
            self.db.clear_user_state(chat_id, user_id)
            return "Главное меню. Выберите действие.", main_menu_keyboard()
        if data == "next":
            return format_events("Ближайшие дни рождения", self.reminders.upcoming(today, limit=10)), main_menu_keyboard()
        if data == "today":
            return format_events("Напоминания на сегодня", self.reminders.due_today(today)), main_menu_keyboard()
        if data == "people":
            return self._people(), people_menu_keyboard()
        if data == "settings":
            return self._settings(), main_menu_keyboard()
        if data == "add_child":
            self.db.set_user_state(chat_id, user_id, "add_name", json.dumps({"role": "child"}))
            return "Добавление ребенка.\n\nВведите ФИО полностью, например: Иванова Анна", cancel_keyboard()
        if data == "add_teacher":
            self.db.set_user_state(chat_id, user_id, "add_name", json.dumps({"role": "teacher"}))
            return "Добавление учителя.\n\nВведите ФИО полностью, например: Швоева Оксана Васильевна", cancel_keyboard()
        if data == "edit":
            self.db.set_user_state(chat_id, user_id, "edit_choose_person")
            return self._people() + "\n\nВведите номер записи, которую нужно изменить.", cancel_keyboard()
        if data == "disable":
            self.db.set_user_state(chat_id, user_id, "disable_choose_person")
            return self._people() + "\n\nВведите номер записи, которую нужно скрыть из напоминаний.", cancel_keyboard()
        if data == "restore":
            self.db.set_user_state(chat_id, user_id, "restore_choose_person")
            return self._people() + "\n\nВведите номер записи, которую нужно вернуть в напоминания.", cancel_keyboard()
        if data == "delete":
            self.db.set_user_state(chat_id, user_id, "delete_choose_person")
            return self._people() + "\n\nВведите номер записи, которую нужно удалить из базы.", cancel_keyboard()
        if data.startswith("edit_field:"):
            person_id = int(data.split(":", 1)[1])
            self.db.set_user_state(chat_id, user_id, "edit_choose_field", json.dumps({"person_id": person_id}))
            return "Что изменить?", edit_field_keyboard(person_id)
        if data.startswith("field:"):
            _, person_id_text, field = data.split(":", 2)
            self.db.set_user_state(
                chat_id,
                user_id,
                "edit_value",
                json.dumps({"person_id": int(person_id_text), "field": field}),
            )
            prompts = {
                "name": "Введите новое ФИО полностью.",
                "date": "Введите новую дату рождения в формате 22.12.2017 или 22.12.",
                "role": "Введите роль: child для ребенка или teacher для учителя.",
                "note": "Введите новое примечание. Чтобы очистить, отправьте один дефис: -",
            }
            return prompts[field], cancel_keyboard()
        if data.startswith("delete_confirm:"):
            person_id = int(data.split(":", 1)[1])
            self.db.clear_user_state(chat_id, user_id)
            deleted = self.db.delete_person(person_id)
            return ("Удалено из базы." if deleted else "Не нашел человека с таким id."), main_menu_keyboard()
        return "Не понял нажатие. Вернитесь в главное меню.", main_menu_keyboard()

    def _handle_state_message(
        self,
        chat_id: int,
        user_id: int,
        text: str,
    ) -> tuple[str, dict | None] | None:
        row = self.db.get_user_state(chat_id, user_id)
        if not row:
            return None
        if not text:
            return "Отправьте текст или нажмите «Отмена».", cancel_keyboard()

        state = row["state"]
        payload = json.loads(row["payload"])

        if state == "add_name":
            payload["name"] = text.strip()
            self.db.set_user_state(chat_id, user_id, "add_date", json.dumps(payload, ensure_ascii=False))
            return "Введите дату рождения в формате 22.12.2017 или 22.12.", cancel_keyboard()

        if state == "add_date":
            parsed = _parse_birth_date(text)
            if not parsed:
                return "Дата должна быть в формате 22.12.2017 или 22.12. Попробуйте еще раз.", cancel_keyboard()
            day, month, year = parsed
            date_error = _validate_birth_date(day, month, year)
            if date_error:
                return date_error, cancel_keyboard()
            payload.update({"day": day, "month": month, "year": year})
            self.db.set_user_state(chat_id, user_id, "add_note", json.dumps(payload, ensure_ascii=False))
            return "Введите примечание, например аллергию. Если примечания нет, отправьте один дефис: -", cancel_keyboard()

        if state == "add_note":
            note = "" if text.strip() == "-" else text.strip()
            try:
                person_id = self.db.add_person(
                    full_name=payload["name"],
                    role=payload["role"],
                    birth_month=payload["month"],
                    birth_day=payload["day"],
                    birth_year=payload["year"],
                    note=note,
                )
            except Exception as exc:
                LOGGER.exception("Could not add person")
                self.db.clear_user_state(chat_id, user_id)
                return f"Не удалось добавить запись: {exc}", main_menu_keyboard()
            self.db.clear_user_state(chat_id, user_id)
            return f"Готово. Добавлена запись id {person_id}: {payload['name']}.", main_menu_keyboard()

        if state == "edit_choose_person":
            person_id = _parse_person_id(text)
            if person_id is None:
                return "Введите только номер из списка, например: 12", cancel_keyboard()
            self.db.set_user_state(chat_id, user_id, "edit_choose_field", json.dumps({"person_id": person_id}))
            return "Что изменить? Нажмите кнопку ниже.", edit_field_keyboard(person_id)

        if state == "edit_value":
            person_id = int(payload["person_id"])
            field = payload["field"]
            updated, message = self._apply_field_update(person_id, field, text)
            self.db.clear_user_state(chat_id, user_id)
            return message if updated else message, main_menu_keyboard()

        if state == "disable_choose_person":
            person_id = _parse_person_id(text)
            if person_id is None:
                return "Введите только номер из списка, например: 12", cancel_keyboard()
            self.db.clear_user_state(chat_id, user_id)
            return ("Отключено." if self.db.disable_person(person_id) else "Не нашел человека с таким id."), main_menu_keyboard()

        if state == "restore_choose_person":
            person_id = _parse_person_id(text)
            if person_id is None:
                return "Введите только номер из списка, например: 12", cancel_keyboard()
            self.db.clear_user_state(chat_id, user_id)
            return ("Включено." if self.db.restore_person(person_id) else "Не нашел человека с таким id."), main_menu_keyboard()

        if state == "delete_choose_person":
            person_id = _parse_person_id(text)
            if person_id is None:
                return "Введите только номер из списка, например: 12", cancel_keyboard()
            self.db.set_user_state(chat_id, user_id, "delete_confirm", json.dumps({"person_id": person_id}))
            return "Удалить запись полностью? Это нельзя отменить.", delete_confirm_keyboard(person_id)

        return None

    def _month(self, rest: str, today: date) -> str:
        month = int(rest) if rest.isdigit() else today.month
        if not 1 <= month <= 12:
            return "Месяц должен быть числом от 1 до 12."
        events = self.reminders.events_for_month(today.year, month)
        return format_events(f"Дни рождения: {MONTHS_GENITIVE[month]} {today.year}", events)

    def _people(self) -> str:
        people = self.db.list_people(active_only=False)
        lines = ["Список людей:"]
        for person in people:
            role = "учитель" if person.role == "teacher" else "ученик"
            status = "" if person.active else " [отключен]"
            year = f".{person.birth_year}" if person.birth_year else ""
            note = f" — {person.note}" if person.note else ""
            lines.append(
                f"{person.id}. {person.full_name} ({role}) — "
                f"{person.birth_day:02d}.{person.birth_month:02d}{year}"
                f"{status}{note}"
            )
        return "\n".join(lines)

    def _add(self, rest: str) -> str:
        match = re.match(
            r"(?P<role>child|teacher)\s+"
            r"(?P<day>\d{1,2})\.(?P<month>\d{1,2})(?:\.(?P<year>\d{4}))?\s+"
            r"(?P<name>[^|]+)(?:\|(?P<note>.+))?$",
            rest,
        )
        if not match:
            return "Формат: /add child 22.12.2017 Фамилия Имя | примечание"
        day = int(match.group("day"))
        month = int(match.group("month"))
        year = int(match.group("year")) if match.group("year") else None
        date_error = _validate_birth_date(day, month, year)
        if date_error:
            return date_error
        try:
            person_id = self.db.add_person(
                full_name=match.group("name").strip(),
                role=match.group("role"),
                birth_month=month,
                birth_day=day,
                birth_year=year,
                note=(match.group("note") or "").strip(),
            )
        except Exception as exc:
            LOGGER.exception("Could not add person")
            return f"Не удалось добавить запись: {exc}"
        return f"Добавлено: id {person_id}."

    def _edit(self, rest: str) -> str:
        match = re.match(r"(?P<id>\d+)\s+(?P<field>name|date|role|note)\s*(?P<value>.*)$", rest)
        if not match:
            return (
                "Форматы:\n"
                "/edit 12 name Новая Фамилия Имя\n"
                "/edit 12 date 22.12.2017\n"
                "/edit 12 role child\n"
                "/edit 12 note Аллергия на ..."
            )
        person_id = int(match.group("id"))
        field = match.group("field")
        value = match.group("value").strip()

        try:
            if field == "name":
                if not value:
                    return "ФИО не должно быть пустым."
                updated = self.db.update_person_field(person_id, "full_name", value)
            elif field == "role":
                if value not in {"child", "teacher"}:
                    return "Роль должна быть child или teacher."
                updated = self.db.update_person_field(person_id, "role", value)
            elif field == "note":
                updated = self.db.update_person_field(person_id, "note", value)
            else:
                parsed = _parse_birth_date(value)
                if not parsed:
                    return "Дата должна быть в формате 22.12 или 22.12.2017."
                day, month, year = parsed
                date_error = _validate_birth_date(day, month, year)
                if date_error:
                    return date_error
                updated_day = self.db.update_person_field(person_id, "birth_day", day)
                self.db.update_person_field(person_id, "birth_month", month)
                self.db.update_person_field(person_id, "birth_year", year)
                updated = updated_day
        except Exception as exc:
            LOGGER.exception("Could not edit person")
            return f"Не удалось обновить запись: {exc}"
        return "Обновлено." if updated else "Не нашел человека с таким id."

    def _apply_field_update(self, person_id: int, field: str, value: str) -> tuple[bool, str]:
        value = value.strip()
        try:
            if field == "name":
                if not value:
                    return False, "ФИО не должно быть пустым."
                updated = self.db.update_person_field(person_id, "full_name", value)
            elif field == "role":
                if value not in {"child", "teacher"}:
                    return False, "Роль должна быть child или teacher."
                updated = self.db.update_person_field(person_id, "role", value)
            elif field == "note":
                updated = self.db.update_person_field(person_id, "note", "" if value == "-" else value)
            elif field == "date":
                parsed = _parse_birth_date(value)
                if not parsed:
                    return False, "Дата должна быть в формате 22.12 или 22.12.2017."
                day, month, year = parsed
                date_error = _validate_birth_date(day, month, year)
                if date_error:
                    return False, date_error
                updated = self.db.update_person_field(person_id, "birth_day", day)
                self.db.update_person_field(person_id, "birth_month", month)
                self.db.update_person_field(person_id, "birth_year", year)
            else:
                return False, "Неизвестное поле."
        except Exception as exc:
            LOGGER.exception("Could not update field")
            return False, f"Не удалось обновить запись: {exc}"
        return updated, "Готово. Данные обновлены." if updated else "Не нашел человека с таким id."

    def _disable(self, rest: str) -> str:
        if not rest.isdigit():
            return "Формат: /disable 12"
        disabled = self.db.disable_person(int(rest))
        return "Отключено." if disabled else "Не нашел человека с таким id."

    def _restore(self, rest: str) -> str:
        if not rest.isdigit():
            return "Формат: /restore 12"
        restored = self.db.restore_person(int(rest))
        return "Включено." if restored else "Не нашел человека с таким id."

    def _delete(self, rest: str) -> str:
        if not rest.isdigit():
            return "Формат: /delete 12"
        deleted = self.db.delete_person(int(rest))
        return "Удалено из базы." if deleted else "Не нашел человека с таким id."

    def _settings(self) -> str:
        return (
            "Настройки:\n"
            f"Чат: {self.config.admin_chat_id}\n"
            f"Время проверки: {self.config.check_time}\n"
            f"Часовой пояс: {self.config.timezone}\n"
            f"База: {self.config.database_path}\n"
            f"Сегодня: {format_date_ru(datetime.now(self.timezone).date())}"
        )


HELP_TEXT = """Команды:
/next — ближайшие дни рождения
/month [1-12] — дни рождения за месяц
/today — напоминания, которые должны уйти сегодня
/people — список людей и id
/add child 22.12.2017 Фамилия Имя | примечание — добавить ребенка
/add teacher 18.08 Фамилия Имя — добавить учителя
/edit 12 name Новая Фамилия Имя — изменить ФИО
/edit 12 date 22.12.2017 — изменить дату рождения
/edit 12 role child — изменить роль child/teacher
/edit 12 note Аллергия на ... — изменить или очистить примечание
/disable 12 — скрыть из напоминаний
/restore 12 — вернуть в напоминания
/delete 12 — удалить из базы
/settings — настройки
/test_reminder — пример сообщения"""


def main_menu_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "Ближайшие ДР", "callback_data": "next"},
                {"text": "Сегодня", "callback_data": "today"},
            ],
            [
                {"text": "Список", "callback_data": "people"},
                {"text": "Настройки", "callback_data": "settings"},
            ],
            [
                {"text": "Добавить ребенка", "callback_data": "add_child"},
                {"text": "Добавить учителя", "callback_data": "add_teacher"},
            ],
            [
                {"text": "Изменить", "callback_data": "edit"},
                {"text": "Скрыть", "callback_data": "disable"},
            ],
            [
                {"text": "Вернуть", "callback_data": "restore"},
                {"text": "Удалить", "callback_data": "delete"},
            ],
        ]
    }


def people_menu_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "Добавить", "callback_data": "add_child"},
                {"text": "Изменить", "callback_data": "edit"},
            ],
            [{"text": "Главное меню", "callback_data": "menu"}],
        ]
    }


def edit_field_keyboard(person_id: int) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "ФИО", "callback_data": f"field:{person_id}:name"},
                {"text": "Дата", "callback_data": f"field:{person_id}:date"},
            ],
            [
                {"text": "Роль", "callback_data": f"field:{person_id}:role"},
                {"text": "Примечание", "callback_data": f"field:{person_id}:note"},
            ],
            [{"text": "Отмена", "callback_data": "menu"}],
        ]
    }


def cancel_keyboard() -> dict:
    return {"inline_keyboard": [[{"text": "Отмена", "callback_data": "menu"}]]}


def delete_confirm_keyboard(person_id: int) -> dict:
    return {
        "inline_keyboard": [
            [{"text": "Да, удалить", "callback_data": f"delete_confirm:{person_id}"}],
            [{"text": "Отмена", "callback_data": "menu"}],
        ]
    }


def _parse_birth_date(value: str) -> tuple[int, int, int | None] | None:
    match = re.fullmatch(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?", value.strip())
    if not match:
        return None
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)) if match.group(3) else None,
    )


def _validate_birth_date(day: int, month: int, year: int | None) -> str | None:
    if year is None:
        year = 2000
    try:
        date(year, month, day)
    except ValueError:
        return "Некорректная дата рождения."
    return None


def _parse_person_id(value: str) -> int | None:
    value = value.strip()
    return int(value) if value.isdigit() else None
