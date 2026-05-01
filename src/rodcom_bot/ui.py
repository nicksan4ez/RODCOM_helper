from __future__ import annotations

from html import escape


def h(value: object) -> str:
    return escape(str(value), quote=False)


def main_menu_text() -> str:
    return (
        "🏠 <b>Главное меню</b>\n\n"
        "Выберите действие кнопкой ниже. Для обычной работы команды помнить не нужно."
    )


def help_text() -> str:
    return (
        "ℹ️ <b>Помощь</b>\n\n"
        "Основной интерфейс работает через кнопки:\n"
        "• посмотреть ближайшие дни рождения;\n"
        "• добавить ребенка или учителя;\n"
        "• изменить ФИО, дату, роль или примечание;\n"
        "• скрыть, вернуть или удалить запись.\n\n"
        "Быстрые команды: /next, /today, /month, /people, /settings."
    )


def done(text: str) -> str:
    return f"✅ <b>Готово</b>\n\n{text}"


def warn(text: str) -> str:
    return f"⚠️ <b>Проверьте данные</b>\n\n{text}"


def error(text: str) -> str:
    return f"❌ <b>Не получилось</b>\n\n{text}"


def prompt(title: str, body: str, example: str | None = None) -> str:
    message = f"✍️ <b>{h(title)}</b>\n\n{h(body)}"
    if example:
        message += f"\n\nПример: <code>{h(example)}</code>"
    return message
