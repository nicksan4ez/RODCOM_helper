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
from .ui import done, error, h, help_text, main_menu_text, prompt, warn


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
            self.telegram.send_message(chat_id, warn("Команда доступна только администраторам родкома."))
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
            self.telegram.send_message(chat_id, warn("Кнопки доступны только администраторам родкома."))
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
            return main_menu_text() + "\n\n" + help_text()
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
        return warn("Неизвестная команда.") + "\n\n" + help_text()

    def _dispatch_callback(
        self,
        chat_id: int,
        user_id: int,
        data: str,
    ) -> tuple[str, dict | None]:
        today = datetime.now(self.timezone).date()
        if data == "menu":
            self.db.clear_user_state(chat_id, user_id)
            return main_menu_text(), main_menu_keyboard()
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
            return prompt("Добавляем ребенка", "Введите ФИО полностью.", "Иванова Анна"), cancel_keyboard()
        if data == "add_teacher":
            self.db.set_user_state(chat_id, user_id, "add_name", json.dumps({"role": "teacher"}))
            return prompt("Добавляем учителя", "Введите ФИО полностью.", "Швоева Оксана Васильевна"), cancel_keyboard()
        if data == "edit":
            self.db.set_user_state(chat_id, user_id, "edit_choose_person")
            return self._people() + "\n\n✍️ Введите номер записи, которую нужно изменить.", cancel_keyboard()
        if data == "disable":
            self.db.set_user_state(chat_id, user_id, "disable_choose_person")
            return self._people() + "\n\n🙈 Введите номер записи, которую нужно скрыть из напоминаний.", cancel_keyboard()
        if data == "restore":
            self.db.set_user_state(chat_id, user_id, "restore_choose_person")
            return self._people() + "\n\n🔄 Введите номер записи, которую нужно вернуть в напоминания.", cancel_keyboard()
        if data == "delete":
            self.db.set_user_state(chat_id, user_id, "delete_choose_person")
            return self._people() + "\n\n🗑️ Введите номер записи, которую нужно удалить из базы.", cancel_keyboard()
        if data.startswith("edit_field:"):
            person_id = int(data.split(":", 1)[1])
            self.db.set_user_state(chat_id, user_id, "edit_choose_field", json.dumps({"person_id": person_id}))
            return "✏️ <b>Что изменить?</b>", edit_field_keyboard(person_id)
        if data.startswith("field:"):
            _, person_id_text, field = data.split(":", 2)
            self.db.set_user_state(
                chat_id,
                user_id,
                "edit_value",
                json.dumps({"person_id": int(person_id_text), "field": field}),
            )
            prompts = {
                "name": prompt("Новое ФИО", "Введите ФИО полностью.", "Иванова Анна"),
                "date": prompt("Новая дата рождения", "Введите дату в формате ДД.ММ.ГГГГ. Год можно не указывать.", "22.12.2017"),
                "role": prompt("Новая роль", "Введите: child для ребенка или teacher для учителя.", "child"),
                "note": prompt("Новое примечание", "Введите примечание. Чтобы очистить, отправьте один дефис.", "-"),
            }
            return prompts[field], cancel_keyboard()
        if data.startswith("delete_confirm:"):
            person_id = int(data.split(":", 1)[1])
            self.db.clear_user_state(chat_id, user_id)
            deleted = self.db.delete_person(person_id)
            return (done("Запись удалена из базы.") if deleted else error("Не нашел человека с таким номером.")), main_menu_keyboard()
        return warn("Не понял нажатие. Вернитесь в главное меню."), main_menu_keyboard()

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
            return warn("Отправьте текст или нажмите «Отмена»."), cancel_keyboard()

        state = row["state"]
        payload = json.loads(row["payload"])

        if state == "add_name":
            payload["name"] = text.strip()
            self.db.set_user_state(chat_id, user_id, "add_date", json.dumps(payload, ensure_ascii=False))
            return prompt("Дата рождения", "Введите дату в формате ДД.ММ.ГГГГ. Год можно не указывать.", "22.12.2017"), cancel_keyboard()

        if state == "add_date":
            parsed = _parse_birth_date(text)
            if not parsed:
                return warn("Дата должна быть в формате 22.12.2017 или 22.12. Попробуйте еще раз."), cancel_keyboard()
            day, month, year = parsed
            date_error = _validate_birth_date(day, month, year)
            if date_error:
                return warn(date_error), cancel_keyboard()
            payload.update({"day": day, "month": month, "year": year})
            self.db.set_user_state(chat_id, user_id, "add_note", json.dumps(payload, ensure_ascii=False))
            return prompt("Примечание", "Введите аллергию или другую важную пометку. Если примечания нет, отправьте один дефис.", "Аллергия на мед"), cancel_keyboard()

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
                return error(f"Не удалось добавить запись: {h(exc)}"), main_menu_keyboard()
            self.db.clear_user_state(chat_id, user_id)
            return done(f"Добавлена запись <code>{person_id}</code>: <b>{h(payload['name'])}</b>."), main_menu_keyboard()

        if state == "edit_choose_person":
            person_id = _parse_person_id(text)
            if person_id is None:
                return warn("Введите только номер из списка, например: 12"), cancel_keyboard()
            self.db.set_user_state(chat_id, user_id, "edit_choose_field", json.dumps({"person_id": person_id}))
            return "✏️ <b>Что изменить?</b>\n\nНажмите нужную кнопку.", edit_field_keyboard(person_id)

        if state == "edit_value":
            person_id = int(payload["person_id"])
            field = payload["field"]
            updated, message = self._apply_field_update(person_id, field, text)
            self.db.clear_user_state(chat_id, user_id)
            return message if updated else message, main_menu_keyboard()

        if state == "disable_choose_person":
            person_id = _parse_person_id(text)
            if person_id is None:
                return warn("Введите только номер из списка, например: 12"), cancel_keyboard()
            self.db.clear_user_state(chat_id, user_id)
            return (done("Запись скрыта из напоминаний.") if self.db.disable_person(person_id) else error("Не нашел человека с таким номером.")), main_menu_keyboard()

        if state == "restore_choose_person":
            person_id = _parse_person_id(text)
            if person_id is None:
                return warn("Введите только номер из списка, например: 12"), cancel_keyboard()
            self.db.clear_user_state(chat_id, user_id)
            return (done("Запись снова участвует в напоминаниях.") if self.db.restore_person(person_id) else error("Не нашел человека с таким номером.")), main_menu_keyboard()

        if state == "delete_choose_person":
            person_id = _parse_person_id(text)
            if person_id is None:
                return warn("Введите только номер из списка, например: 12"), cancel_keyboard()
            self.db.set_user_state(chat_id, user_id, "delete_confirm", json.dumps({"person_id": person_id}))
            return "🗑️ <b>Удалить запись полностью?</b>\n\nЭто действие нельзя отменить.", delete_confirm_keyboard(person_id)

        return None

    def _month(self, rest: str, today: date) -> str:
        month = int(rest) if rest.isdigit() else today.month
        if not 1 <= month <= 12:
            return warn("Месяц должен быть числом от 1 до 12.")
        events = self.reminders.events_for_month(today.year, month)
        return format_events(f"Дни рождения: {MONTHS_GENITIVE[month]} {today.year}", events)

    def _people(self) -> str:
        people = self.db.list_people(active_only=False)
        lines = ["👥 <b>Список людей</b>\n"]
        for person in people:
            role = "👩‍🏫 учитель" if person.role == "teacher" else "🎒 ученик"
            status = "" if person.active else " · 🙈 скрыт"
            year = f".{person.birth_year}" if person.birth_year else ""
            note = f"\n   📝 {h(person.note)}" if person.note else ""
            lines.append(
                f"<code>{person.id}</code> · <b>{h(person.full_name)}</b>\n"
                f"   {role} · 🎂 {person.birth_day:02d}.{person.birth_month:02d}{year}"
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
            return warn("Формат: /add child 22.12.2017 Фамилия Имя | примечание")
        day = int(match.group("day"))
        month = int(match.group("month"))
        year = int(match.group("year")) if match.group("year") else None
        date_error = _validate_birth_date(day, month, year)
        if date_error:
            return warn(date_error)
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
            return error(f"Не удалось добавить запись: {h(exc)}")
        return done(f"Добавлена запись <code>{person_id}</code>.")

    def _edit(self, rest: str) -> str:
        match = re.match(r"(?P<id>\d+)\s+(?P<field>name|date|role|note)\s*(?P<value>.*)$", rest)
        if not match:
            return warn(
                "Форматы:\n"
                "<code>/edit 12 name Новая Фамилия Имя</code>\n"
                "<code>/edit 12 date 22.12.2017</code>\n"
                "<code>/edit 12 role child</code>\n"
                "<code>/edit 12 note Аллергия на ...</code>"
            )
        person_id = int(match.group("id"))
        field = match.group("field")
        value = match.group("value").strip()

        try:
            if field == "name":
                if not value:
                    return warn("ФИО не должно быть пустым.")
                updated = self.db.update_person_field(person_id, "full_name", value)
            elif field == "role":
                if value not in {"child", "teacher"}:
                    return warn("Роль должна быть child или teacher.")
                updated = self.db.update_person_field(person_id, "role", value)
            elif field == "note":
                updated = self.db.update_person_field(person_id, "note", value)
            else:
                parsed = _parse_birth_date(value)
                if not parsed:
                    return warn("Дата должна быть в формате 22.12 или 22.12.2017.")
                day, month, year = parsed
                date_error = _validate_birth_date(day, month, year)
                if date_error:
                    return warn(date_error)
                updated_day = self.db.update_person_field(person_id, "birth_day", day)
                self.db.update_person_field(person_id, "birth_month", month)
                self.db.update_person_field(person_id, "birth_year", year)
                updated = updated_day
        except Exception as exc:
            LOGGER.exception("Could not edit person")
            return error(f"Не удалось обновить запись: {h(exc)}")
        return done("Данные обновлены.") if updated else error("Не нашел человека с таким номером.")

    def _apply_field_update(self, person_id: int, field: str, value: str) -> tuple[bool, str]:
        value = value.strip()
        try:
            if field == "name":
                if not value:
                    return False, warn("ФИО не должно быть пустым.")
                updated = self.db.update_person_field(person_id, "full_name", value)
            elif field == "role":
                if value not in {"child", "teacher"}:
                    return False, warn("Роль должна быть child или teacher.")
                updated = self.db.update_person_field(person_id, "role", value)
            elif field == "note":
                updated = self.db.update_person_field(person_id, "note", "" if value == "-" else value)
            elif field == "date":
                parsed = _parse_birth_date(value)
                if not parsed:
                    return False, warn("Дата должна быть в формате 22.12 или 22.12.2017.")
                day, month, year = parsed
                date_error = _validate_birth_date(day, month, year)
                if date_error:
                    return False, warn(date_error)
                updated = self.db.update_person_field(person_id, "birth_day", day)
                self.db.update_person_field(person_id, "birth_month", month)
                self.db.update_person_field(person_id, "birth_year", year)
            else:
                return False, error("Неизвестное поле.")
        except Exception as exc:
            LOGGER.exception("Could not update field")
            return False, error(f"Не удалось обновить запись: {h(exc)}")
        return updated, done("Данные обновлены.") if updated else error("Не нашел человека с таким номером.")

    def _disable(self, rest: str) -> str:
        if not rest.isdigit():
            return warn("Формат: /disable 12")
        disabled = self.db.disable_person(int(rest))
        return done("Запись скрыта из напоминаний.") if disabled else error("Не нашел человека с таким номером.")

    def _restore(self, rest: str) -> str:
        if not rest.isdigit():
            return warn("Формат: /restore 12")
        restored = self.db.restore_person(int(rest))
        return done("Запись снова участвует в напоминаниях.") if restored else error("Не нашел человека с таким номером.")

    def _delete(self, rest: str) -> str:
        if not rest.isdigit():
            return warn("Формат: /delete 12")
        deleted = self.db.delete_person(int(rest))
        return done("Запись удалена из базы.") if deleted else error("Не нашел человека с таким номером.")

    def _settings(self) -> str:
        return (
            "⚙️ <b>Настройки</b>\n\n"
            f"💬 Чат: <code>{h(self.config.admin_chat_id)}</code>\n"
            f"⏰ Проверка: <b>{h(self.config.check_time)}</b>\n"
            f"🌏 Часовой пояс: <code>{h(self.config.timezone)}</code>\n"
            f"🗄️ База: <code>{h(self.config.database_path)}</code>\n"
            f"📅 Сегодня: {h(format_date_ru(datetime.now(self.timezone).date()))}"
        )


HELP_TEXT = help_text()


def main_menu_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "🎂 Ближайшие ДР", "callback_data": "next"},
                {"text": "🔔 Сегодня", "callback_data": "today"},
            ],
            [
                {"text": "👥 Список", "callback_data": "people"},
                {"text": "⚙️ Настройки", "callback_data": "settings"},
            ],
            [
                {"text": "➕ Ребенок", "callback_data": "add_child"},
                {"text": "➕ Учитель", "callback_data": "add_teacher"},
            ],
            [
                {"text": "✏️ Изменить", "callback_data": "edit"},
                {"text": "🙈 Скрыть", "callback_data": "disable"},
            ],
            [
                {"text": "🔄 Вернуть", "callback_data": "restore"},
                {"text": "🗑️ Удалить", "callback_data": "delete"},
            ],
        ]
    }


def people_menu_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "➕ Добавить", "callback_data": "add_child"},
                {"text": "✏️ Изменить", "callback_data": "edit"},
            ],
            [{"text": "🏠 Главное меню", "callback_data": "menu"}],
        ]
    }


def edit_field_keyboard(person_id: int) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "👤 ФИО", "callback_data": f"field:{person_id}:name"},
                {"text": "🎂 Дата", "callback_data": f"field:{person_id}:date"},
            ],
            [
                {"text": "🏷️ Роль", "callback_data": f"field:{person_id}:role"},
                {"text": "📝 Примечание", "callback_data": f"field:{person_id}:note"},
            ],
            [{"text": "↩️ Отмена", "callback_data": "menu"}],
        ]
    }


def cancel_keyboard() -> dict:
    return {"inline_keyboard": [[{"text": "↩️ Отмена", "callback_data": "menu"}]]}


def delete_confirm_keyboard(person_id: int) -> dict:
    return {
        "inline_keyboard": [
            [{"text": "🗑️ Да, удалить", "callback_data": f"delete_confirm:{person_id}"}],
            [{"text": "↩️ Отмена", "callback_data": "menu"}],
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
