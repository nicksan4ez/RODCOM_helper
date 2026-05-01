from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .calendar_ru import WorkCalendar
from .config import Config
from .db import Database
from .docx_import import import_people_from_docx
from .formatting import MONTHS_GENITIVE, format_date_ru
from .report_xlsx import build_collections_report
from .reminders import ReminderService, format_events, format_today_events
from .telegram_api import TelegramClient
from .ui import done, error, h, help_text, main_menu_text, prompt, warn
from .yandex_disk import YandexDiskClient


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
        LOGGER.info("Starting RODCOM bot")
        LOGGER.info("Database path: %s", self.config.database_path)
        LOGGER.info("Timezone: %s, default daily check time: %s", self.config.timezone, self.config.check_time)

        people_before_import = self.db.list_people(active_only=False)
        if not people_before_import:
            if self.config.source_docx_path is not None and self.config.source_docx_path.exists():
                LOGGER.info("Database is empty, importing initial people from %s", self.config.source_docx_path)
                people = import_people_from_docx(self.config.source_docx_path)
                inserted = self.db.seed_people(people)
                LOGGER.info("Imported %s people from %s", inserted, self.config.source_docx_path)
            else:
                LOGGER.info("Database is empty and SOURCE_DOCX_PATH is not provided; skipping DOCX import")
        else:
            LOGGER.info("Database already contains %s people, skipping DOCX import", len(people_before_import))

        self.db.set_setting("timezone", self.config.timezone)
        if self.db.get_setting("check_time") is None:
            self.db.set_setting("check_time", self.config.check_time)
        self.db.set_setting("admin_chat_id", self.config.admin_chat_id)
        active_count = len(self.db.list_people(active_only=True))
        total_count = len(self.db.list_people(active_only=False))
        LOGGER.info("People in database: %s active, %s total", active_count, total_count)
        LOGGER.info("Active daily check time: %s", self._check_time())

    def run(self) -> None:
        self.bootstrap()
        scheduler = threading.Thread(target=self._scheduler_loop, daemon=True)
        scheduler.start()
        LOGGER.info("Scheduler started")
        LOGGER.info("Telegram polling started")
        self._poll_loop()

    def _scheduler_loop(self) -> None:
        last_run: str | None = None
        while not self._stop.is_set():
            now = datetime.now(self.timezone)
            today_key = now.date().isoformat()
            if now.strftime("%H:%M") == self._check_time() and last_run != today_key:
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
            status_text = "⏳ Обновляю отчет, это может занять несколько секунд..." if data == "collection_report" else None
            self.telegram.answer_callback_query(callback_id, status_text)
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
            return format_events("Ближайшие дни рождения", self.reminders.upcoming(today, limit=3))
        if command == "/today":
            return format_today_events("Сегодня", self.reminders.matching_today(today), today)
        if command == "/month":
            return self._month(rest, today)
        if command == "/people":
            return self._people(sort_by="name")
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
        if command == "/collections":
            return self._collections()
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
            return format_events("Ближайшие дни рождения", self.reminders.upcoming(today, limit=3)), main_menu_keyboard()
        if data == "today":
            return format_today_events("Сегодня", self.reminders.matching_today(today), today), main_menu_keyboard()
        if data == "people":
            return self._people(sort_by="name"), people_menu_keyboard(active_sort="name")
        if data == "people_birthdays":
            return self._people(sort_by="birthday"), people_menu_keyboard(active_sort="birthday")
        if data == "people_name":
            return self._people(sort_by="name"), people_menu_keyboard(active_sort="name")
        if data == "settings":
            return self._settings(), settings_keyboard()
        if data == "collections":
            return self._collections(), self._collections_keyboard()
        if data == "collection_archive":
            return self._collections_archive(), self._collection_archive_keyboard()
        if data == "collection_report":
            return self._update_collections_report(), self._collections_keyboard()
        if data.startswith("collection_open:"):
            collection_id = int(data.split(":", 1)[1])
            return self._collection_detail(collection_id), collection_detail_keyboard(collection_id)
        if data == "collection_new":
            self.db.set_user_state(chat_id, user_id, "collection_new_title")
            return prompt("Новый сбор", "Введите название сбора.", "Экскурсия в музей"), cancel_keyboard()
        if data.startswith("collection_pay:"):
            collection_id = int(data.split(":", 1)[1])
            self.db.set_user_state(chat_id, user_id, "collection_pay_person", json.dumps({"collection_id": collection_id}))
            return self._collection_members_prompt(collection_id, only_unpaid=True, action="отметить оплату"), cancel_keyboard()
        if data.startswith("collection_unpay:"):
            collection_id = int(data.split(":", 1)[1])
            self.db.set_user_state(chat_id, user_id, "collection_unpay_person", json.dumps({"collection_id": collection_id}))
            return self._collection_members_prompt(collection_id, only_unpaid=False, action="отменить оплату"), cancel_keyboard()
        if data.startswith("collection_debtors:"):
            collection_id = int(data.split(":", 1)[1])
            return self._collection_debtors(collection_id), collection_detail_keyboard(collection_id)
        if data.startswith("collection_close:"):
            collection_id = int(data.split(":", 1)[1])
            return (
                "🙈 <b>Скрыть сбор?</b>\n\n"
                "Он пропадет из активного списка бота, но останется в базе и будет попадать в отчет."
            ), collection_close_confirm_keyboard(collection_id)
        if data.startswith("collection_close_confirm:"):
            collection_id = int(data.split(":", 1)[1])
            closed = self.db.close_collection(collection_id)
            return (done("Сбор скрыт. Он останется в отчете.") if closed else error("Не нашел сбор.")), self._collections_keyboard()
        if data.startswith("collection_delete:"):
            collection_id = int(data.split(":", 1)[1])
            return (
                "🗑️ <b>Удалить сбор полностью?</b>\n\n"
                "Он исчезнет из базы и больше не будет попадать в отчеты. Это действие нельзя отменить."
            ), collection_delete_confirm_keyboard(collection_id)
        if data.startswith("collection_delete_confirm:"):
            collection_id = int(data.split(":", 1)[1])
            deleted = self.db.delete_collection(collection_id)
            return (done("Сбор удален из базы и отчетов.") if deleted else error("Не нашел сбор.")), self._collections_keyboard()
        if data == "settings_time":
            self.db.set_user_state(chat_id, user_id, "settings_time")
            return prompt("Время напоминаний", "Введите время ежедневной проверки в формате ЧЧ:ММ.", "07:30"), cancel_keyboard()
        if data == "add_child":
            self.db.set_user_state(chat_id, user_id, "add_name", json.dumps({"role": "child"}))
            return prompt("Добавляем ребенка", "Введите ФИО полностью.", "Иванова Анна"), cancel_keyboard()
        if data == "add_teacher":
            self.db.set_user_state(chat_id, user_id, "add_name", json.dumps({"role": "teacher"}))
            return prompt("Добавляем учителя", "Введите ФИО полностью.", "Швоева Оксана Васильевна"), cancel_keyboard()
        if data == "edit":
            self.db.set_user_state(chat_id, user_id, "edit_choose_person")
            return self._people(sort_by="name") + "\n\n✍️ Введите номер записи, которую нужно изменить.", cancel_keyboard()
        if data == "disable":
            self.db.set_user_state(chat_id, user_id, "disable_choose_person")
            return self._people(sort_by="name") + "\n\n🙈 Введите номер записи, которую нужно скрыть из напоминаний.", cancel_keyboard()
        if data == "restore":
            self.db.set_user_state(chat_id, user_id, "restore_choose_person")
            return self._people(sort_by="name") + "\n\n🔄 Введите номер записи, которую нужно вернуть в напоминания.", cancel_keyboard()
        if data == "delete":
            self.db.set_user_state(chat_id, user_id, "delete_choose_person")
            return self._people(sort_by="name") + "\n\n🗑️ Введите номер записи, которую нужно удалить из базы.", cancel_keyboard()
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

        if state == "settings_time":
            parsed_time = _parse_time(text)
            if parsed_time is None:
                return warn("Введите время в формате ЧЧ:ММ, например 07:30."), cancel_keyboard()
            self.db.set_setting("check_time", parsed_time)
            self.db.clear_user_state(chat_id, user_id)
            LOGGER.info("Daily check time changed to %s", parsed_time)
            return done(f"Время напоминаний изменено на <b>{h(parsed_time)}</b>."), settings_keyboard()

        if state == "collection_new_title":
            title = text.strip()
            if not title:
                return warn("Название сбора не должно быть пустым."), cancel_keyboard()
            self.db.set_user_state(
                chat_id,
                user_id,
                "collection_new_amount",
                json.dumps({"title": title}, ensure_ascii=False),
            )
            return prompt("Сумма сбора", "Введите сумму с одного ученика в рублях.", "1000"), cancel_keyboard()

        if state == "collection_new_amount":
            amount = _parse_amount(text)
            if amount is None or amount <= 0:
                return warn("Введите сумму числом, например 1000."), cancel_keyboard()
            title = payload["title"]
            try:
                collection_id = self.db.create_collection_for_active_children(title, amount)
            except Exception as exc:
                LOGGER.exception("Could not create collection")
                self.db.clear_user_state(chat_id, user_id)
                return error(f"Не удалось создать сбор: {h(exc)}"), self._collections_keyboard()
            self.db.clear_user_state(chat_id, user_id)
            return done(f"Создан сбор <b>{h(title)}</b> на сумму <b>{amount} ₽</b> с ученика.\nНомер сбора: <code>{collection_id}</code>"), self._collections_keyboard()

        if state == "collection_pay_person":
            person_ids = _parse_person_ids(text)
            collection_id = int(payload["collection_id"])
            if not person_ids:
                return warn("Введите номера через запятую, например: 1,2,3."), cancel_keyboard()
            summary = self.db.get_collection_summary(collection_id)
            if summary is None:
                self.db.clear_user_state(chat_id, user_id)
                return error("Не нашел сбор."), self._collections_keyboard()
            updated_count = sum(
                1
                for person_id in person_ids
                if self.db.set_collection_payment(collection_id, person_id, summary.collection.expected_amount)
            )
            self.db.clear_user_state(chat_id, user_id)
            return (
                done(f"Отмечено оплат: <b>{updated_count}</b>.")
                if updated_count
                else error("Не нашел указанные номера в этом сборе.")
            ), collection_detail_keyboard(collection_id)

        if state == "collection_unpay_person":
            person_ids = _parse_person_ids(text)
            collection_id = int(payload["collection_id"])
            if not person_ids:
                return warn("Введите номера через запятую, например: 1,2,3."), cancel_keyboard()
            updated_count = sum(
                1
                for person_id in person_ids
                if self.db.set_collection_payment(collection_id, person_id, 0)
            )
            self.db.clear_user_state(chat_id, user_id)
            return (
                done(f"Отменено оплат: <b>{updated_count}</b>.")
                if updated_count
                else error("Не нашел указанные номера в этом сборе.")
            ), collection_detail_keyboard(collection_id)

        return None

    def _month(self, rest: str, today: date) -> str:
        month = int(rest) if rest.isdigit() else today.month
        if not 1 <= month <= 12:
            return warn("Месяц должен быть числом от 1 до 12.")
        events = self.reminders.events_for_month(today.year, month)
        return format_events(f"Дни рождения: {MONTHS_GENITIVE[month]} {today.year}", events)

    def _people(self, sort_by: str = "name") -> str:
        people = self.db.list_people(active_only=False)
        teachers = [person for person in people if person.role == "teacher"]
        students = [person for person in people if person.role != "teacher"]
        teachers = _sort_people(teachers, sort_by)
        students = _sort_people(students, sort_by)

        sort_label = "по ФИО" if sort_by == "name" else "по дате рождения"
        lines = [f"👥 <b>Список класса</b>\nСортировка: <b>{h(sort_label)}</b>"]
        if teachers:
            lines.append("\n👩‍🏫 <b>Учитель</b>")
            lines.extend(_format_person_line(person) for person in teachers)
        if students:
            lines.append("\n🎒 <b>Ученики</b>")
            lines.extend(_format_person_line(person) for person in students)
        if not people:
            lines.append("\nСписок пока пуст.")
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
            f"⏰ Время напоминаний: <b>{h(self._check_time())}</b>\n"
            f"🌏 Часовой пояс: <code>{h(self.config.timezone)}</code>\n"
            f"📅 Сегодня: {h(format_date_ru(datetime.now(self.timezone).date()))}"
        )

    def _check_time(self) -> str:
        return self.db.get_setting("check_time", self.config.check_time) or self.config.check_time

    def _collections(self) -> str:
        summaries = self.db.list_collection_summaries(active_only=True)
        if not summaries:
            return "💰 <b>Сборы</b>\n\nАктивных сборов пока нет."
        lines = ["💰 <b>Сборы</b>\n"]
        for summary in summaries:
            remaining = summary.expected_total - summary.paid_total
            lines.append(
                f"<code>{summary.collection.id}</code> · <b>{h(summary.collection.title)}</b>\n"
                f"   👥 Сдали: <b>{summary.paid_count}/{summary.members_count}</b>\n"
                f"   💵 Сумма: {summary.collection.expected_amount} ₽ с ученика\n"
                f"   ✅ Собрано: {summary.paid_total} ₽\n"
                f"   ⏳ Осталось: {remaining} ₽"
            )
        return "\n\n".join(lines)

    def _collections_keyboard(self) -> dict:
        summaries = self.db.list_collection_summaries(active_only=False)
        active = [summary for summary in summaries if summary.collection.status == "active"]
        has_closed = any(summary.collection.status == "closed" for summary in summaries)
        return collections_keyboard(active, has_closed=has_closed)

    def _collections_archive(self) -> str:
        summaries = [
            summary
            for summary in self.db.list_collection_summaries(active_only=False)
            if summary.collection.status == "closed"
        ]
        if not summaries:
            return "🗃️ <b>Скрытые сборы</b>\n\nСкрытых сборов пока нет."
        lines = ["🗃️ <b>Скрытые сборы</b>\n"]
        for summary in summaries:
            remaining = summary.expected_total - summary.paid_total
            lines.append(
                f"<code>{summary.collection.id}</code> · <b>{h(summary.collection.title)}</b>\n"
                f"   👥 Сдали: <b>{summary.paid_count}/{summary.members_count}</b>\n"
                f"   ✅ Собрано: {summary.paid_total} ₽\n"
                f"   ⏳ Осталось: {remaining} ₽\n"
                f"   📄 В отчете: да"
            )
        return "\n\n".join(lines)

    def _collection_archive_keyboard(self) -> dict:
        summaries = [
            summary
            for summary in self.db.list_collection_summaries(active_only=False)
            if summary.collection.status == "closed"
        ]
        return collection_archive_keyboard(summaries)

    def _update_collections_report(self) -> str:
        if not self.db.list_collection_summaries(active_only=False):
            return warn("Сборов пока нет. Сначала создайте или импортируйте сбор.")
        if not self.config.yandex_disk_token or not self.config.yandex_disk_report_path:
            return warn(
                "Отчет сформировать можно, но загрузка на Яндекс.Диск не настроена.\n\n"
                "Нужно заполнить <code>YANDEX_DISK_TOKEN</code> и <code>YANDEX_DISK_REPORT_PATH</code> в .env."
            )

        report_path = Path("/tmp/rodcom_collections_report.xlsx")
        disk_path = _report_disk_path(self.config.yandex_disk_report_path)
        try:
            build_collections_report(self.db, report_path)
            yandex_disk = YandexDiskClient(self.config.yandex_disk_token)
            public_url = yandex_disk.publish_resource(_report_folder_path(disk_path))
            yandex_disk.upload_file(report_path, disk_path)
        except Exception as exc:
            LOGGER.exception("Could not update Yandex Disk collections report")
            return error(f"Не удалось обновить отчет: {h(exc)}")
        return done(
            "Отчет по сборам обновлен на Яндекс.Диске.\n\n"
            f"Файл: <code>{h(disk_path)}</code>\n"
            f"🔗 Доступен по ссылке: {h(public_url)}"
        )

    def _collection_detail(self, collection_id: int) -> str:
        summary = self.db.get_collection_summary(collection_id)
        if summary is None:
            return error("Не нашел сбор.")
        remaining = summary.expected_total - summary.paid_total
        debtors = [member for member in self.db.list_collection_members(collection_id) if member.status != "paid"]
        lines = [
            f"💰 <b>{h(summary.collection.title)}</b>",
            "",
            f"📌 Статус: <b>{'активный' if summary.collection.status == 'active' else 'скрытый'}</b>",
            f"👥 Сдали: <b>{summary.paid_count}/{summary.members_count}</b>",
            f"💵 Сумма: {summary.collection.expected_amount} ₽ с ученика",
            f"✅ Собрано: {summary.paid_total} ₽",
            f"⏳ Осталось: {remaining} ₽",
        ]
        if debtors:
            lines.append("\n❌ <b>Не сдали</b>")
            lines.extend(f"<code>{member.person_id}</code> · {h(member.full_name)}" for member in debtors[:20])
            if len(debtors) > 20:
                lines.append(f"...и еще {len(debtors) - 20}")
        else:
            lines.append("\n✅ Все сдали.")
        return "\n".join(lines)

    def _collection_debtors(self, collection_id: int) -> str:
        summary = self.db.get_collection_summary(collection_id)
        if summary is None:
            return error("Не нашел сбор.")
        debtors = [member for member in self.db.list_collection_members(collection_id) if member.status != "paid"]
        if not debtors:
            return f"✅ <b>{h(summary.collection.title)}</b>\n\nВсе сдали."
        plain_lines = [
            "Добрый день!",
            "",
            f"Напоминаем про сбор «{summary.collection.title}».",
            f"Сумма: {summary.collection.expected_amount} ₽.",
            "",
            "Пока не сдали:",
        ]
        plain_lines.extend(f"- {member.full_name}" for member in debtors)
        plain_lines.extend(["", "Спасибо!"])
        return (
            f"📋 <b>Сообщение для родительского чата</b>\n\n"
            f"<pre>{h(chr(10).join(plain_lines))}</pre>"
        )

    def _collection_members_prompt(self, collection_id: int, only_unpaid: bool, action: str) -> str:
        summary = self.db.get_collection_summary(collection_id)
        if summary is None:
            return error("Не нашел сбор.")
        members = self.db.list_collection_members(collection_id)
        if only_unpaid:
            members = [member for member in members if member.status != "paid"]
        else:
            members = [member for member in members if member.status == "paid"]
        if not members:
            return warn("Подходящих записей нет.")
        lines = [f"✍️ <b>{h(action.capitalize())}</b>\n", f"Сбор: <b>{h(summary.collection.title)}</b>\n"]
        for member in members:
            status = "✅" if member.status == "paid" else "❌"
            lines.append(f"<code>{member.person_id}</code> · {status} {h(member.full_name)}")
        if only_unpaid:
            lines.append("\nВведите номера тех, кто сдал, например: <code>1,2,3</code>.")
        else:
            lines.append("\nВведите номера, у кого нужно отменить оплату, например: <code>1,2,3</code>.")
        return "\n".join(lines)


def _sort_people(people, sort_by: str):
    if sort_by == "birthday":
        return sorted(people, key=lambda person: (person.birth_month, person.birth_day, person.full_name.lower()))
    return sorted(people, key=lambda person: person.full_name.lower())


def _format_person_line(person) -> str:
    role = "👩‍🏫 учитель" if person.role == "teacher" else "🎒 ученик"
    status = "" if person.active else " · 🙈 скрыт"
    year = f".{person.birth_year}" if person.birth_year else ""
    note = f"\n   📝 {h(person.note)}" if person.note else ""
    return (
        f"<code>{person.id}</code> · <b>{h(person.full_name)}</b>\n"
        f"   {role} · 🎂 {person.birth_day:02d}.{person.birth_month:02d}{year}"
        f"{status}{note}"
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
                {"text": "💰 Сборы", "callback_data": "collections"},
            ],
            [{"text": "⚙️ Настройки", "callback_data": "settings"}],
        ]
    }


def collections_keyboard(summaries=None, has_closed: bool = False) -> dict:
    rows = []
    for summary in summaries or []:
        rows.append([{"text": f"💰 {summary.collection.title}", "callback_data": f"collection_open:{summary.collection.id}"}])
    if has_closed:
        rows.append([{"text": "🗃️ Скрытые сборы", "callback_data": "collection_archive"}])
    rows.append([{"text": "📤 Обновить отчет", "callback_data": "collection_report"}])
    rows.append([{"text": "➕ Новый сбор", "callback_data": "collection_new"}])
    rows.append([{"text": "🏠 Главное меню", "callback_data": "menu"}])
    return {"inline_keyboard": rows}


def collection_archive_keyboard(summaries=None) -> dict:
    rows = []
    for summary in summaries or []:
        rows.append([{"text": f"🗃️ {summary.collection.title}", "callback_data": f"collection_open:{summary.collection.id}"}])
    rows.append([{"text": "💰 Активные сборы", "callback_data": "collections"}])
    rows.append([{"text": "🏠 Главное меню", "callback_data": "menu"}])
    return {"inline_keyboard": rows}


def collection_detail_keyboard(collection_id: int) -> dict:
    return {
        "inline_keyboard": [
            [{"text": "✅ Сдали", "callback_data": f"collection_pay:{collection_id}"}],
            [{"text": "↩️ Отменить оплату", "callback_data": f"collection_unpay:{collection_id}"}],
            [{"text": "📋 Сообщение должникам", "callback_data": f"collection_debtors:{collection_id}"}],
            [{"text": "🙈 Скрыть сбор", "callback_data": f"collection_close:{collection_id}"}],
            [{"text": "🗑️ Удалить сбор", "callback_data": f"collection_delete:{collection_id}"}],
            [{"text": "💰 Все сборы", "callback_data": "collections"}],
        ]
    }


def collection_close_confirm_keyboard(collection_id: int) -> dict:
    return {
        "inline_keyboard": [
            [{"text": "🙈 Да, скрыть", "callback_data": f"collection_close_confirm:{collection_id}"}],
            [{"text": "↩️ Назад к сбору", "callback_data": f"collection_open:{collection_id}"}],
        ]
    }


def collection_delete_confirm_keyboard(collection_id: int) -> dict:
    return {
        "inline_keyboard": [
            [{"text": "🗑️ Да, удалить", "callback_data": f"collection_delete_confirm:{collection_id}"}],
            [{"text": "↩️ Назад к сбору", "callback_data": f"collection_open:{collection_id}"}],
        ]
    }


def people_menu_keyboard(active_sort: str = "name") -> dict:
    name_text = "☑️ По ФИО" if active_sort == "name" else "⬜️ По ФИО"
    birthday_text = "☑️ По ДР" if active_sort == "birthday" else "⬜️ По ДР"
    return {
        "inline_keyboard": [
            [
                {"text": name_text, "callback_data": "people_name"},
                {"text": birthday_text, "callback_data": "people_birthdays"},
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
            [{"text": "🏠 Главное меню", "callback_data": "menu"}],
        ]
    }


def settings_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [{"text": "⏰ Изменить время", "callback_data": "settings_time"}],
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


def _parse_time(value: str) -> str | None:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", value.strip())
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def _parse_amount(value: str) -> int | None:
    match = re.search(r"\d+(?:[.,]\d+)?", value.replace(" ", ""))
    if not match:
        return None
    return int(round(float(match.group(0).replace(",", "."))))


def _parse_person_id(value: str) -> int | None:
    value = value.strip()
    return int(value) if value.isdigit() else None


def _parse_person_ids(value: str) -> list[int]:
    ids = []
    for part in re.split(r"[\s,;]+", value.strip()):
        if part.isdigit():
            ids.append(int(part))
        elif part:
            return []
    return list(dict.fromkeys(ids))


def _report_disk_path(value: str) -> str:
    value = value.strip()
    if value.endswith("/"):
        return value + "sbori_report.xlsx"
    if not value.lower().endswith(".xlsx"):
        return value + "/sbori_report.xlsx"
    return value


def _report_folder_path(disk_path: str) -> str:
    folder = disk_path.rstrip("/").rsplit("/", 1)[0]
    return folder or "/"
