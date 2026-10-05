from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo


MOSCOW_TZ = ZoneInfo("Europe/Moscow")
DEFAULT_ADMIN_USER_ID = 7789152483


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    admin_user_id: int
    database_path: Path
    app_env: str
    log_level: str

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError(
                "Не задан BOT_TOKEN. Добавьте секрет BOT_TOKEN в Replit Secrets."
            )
        admin_raw = os.getenv("ADMIN_USER_ID", str(DEFAULT_ADMIN_USER_ID)).strip()
        try:
            admin_user_id = int(admin_raw)
        except ValueError as error:
            raise RuntimeError("ADMIN_USER_ID должен быть целым числом.") from error

        database_path_raw = os.getenv("DATABASE_PATH", "").strip()
        if database_path_raw:
            database_path = Path(database_path_raw).expanduser()
        else:
            database_url = os.getenv("DATABASE_URL", "").strip()
        if not database_path_raw and database_url:
            parsed = urlsplit(database_url)
            if parsed.scheme not in {"sqlite", "sqlite3"}:
                raise RuntimeError(
                    "Сейчас поддерживается SQLite через DATABASE_PATH или "
                    "sqlite:///... в DATABASE_URL. Для PostgreSQL нужен отдельный адаптер."
                )
            if database_url.startswith(("sqlite:////", "sqlite3:////")):
                database_name = "/" + unquote(parsed.path).lstrip("/")
            else:
                database_name = unquote(parsed.path).lstrip("/")
            if not database_name or database_name == "/":
                raise RuntimeError("В DATABASE_URL не указан путь к SQLite-файлу.")
            database_path = Path(database_name).expanduser()
        elif not database_path_raw:
            database_path = Path(
                os.getenv("DATABASE_PATH", "data/nefor_spot.sqlite3")
            ).expanduser()
        return cls(
            bot_token=token,
            admin_user_id=admin_user_id,
            database_path=database_path,
            app_env=os.getenv("APP_ENV", "development").strip().lower(),
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
        )
