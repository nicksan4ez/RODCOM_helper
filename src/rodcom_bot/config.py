from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_chat_id: str
    admin_user_ids: set[int]
    timezone: str
    check_time: str
    database_path: Path
    source_docx_path: Path | None

    @classmethod
    def from_env(cls) -> "Config":
        bot_token = _required("BOT_TOKEN")
        admin_chat_id = _required("ADMIN_CHAT_ID")
        admin_user_ids = {
            int(value.strip())
            for value in os.getenv("ADMIN_USER_IDS", "").split(",")
            if value.strip()
        }
        source_docx_path = os.getenv("SOURCE_DOCX_PATH", "").strip()
        return cls(
            bot_token=bot_token,
            admin_chat_id=admin_chat_id,
            admin_user_ids=admin_user_ids,
            timezone=os.getenv("TIMEZONE", "Asia/Vladivostok"),
            check_time=os.getenv("CHECK_TIME", "07:30"),
            database_path=Path(os.getenv("DATABASE_PATH", "/data/rodcom.sqlite3")),
            source_docx_path=Path(source_docx_path) if source_docx_path else None,
        )


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Environment variable {name} is required")
    return value
