from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


MOSCOW_TZ = ZoneInfo("Europe/Moscow")
DEFAULT_ADMIN_USER_ID = 7789152483


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    admin_user_id: int
    database_path: Path
    database_url: str
    app_env: str
    log_level: str

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError("Не задан BOT_TOKEN.")

        admin_raw = os.getenv(
            "ADMIN_USER_ID",
            str(DEFAULT_ADMIN_USER_ID),
        ).strip()

        try:
            admin_user_id = int(admin_raw)
        except ValueError as error:
            raise RuntimeError(
                "ADMIN_USER_ID должен быть целым числом."
            ) from error

        database_url = os.getenv("DATABASE_URL", "").strip()

        database_path = Path(
            os.getenv(
                "DATABASE_PATH",
                "data/nefor_spot.sqlite3",
            )
        ).expanduser()

        return cls(
            bot_token=token,
            admin_user_id=admin_user_id,
            database_path=database_path,
            database_url=database_url,
            app_env=os.getenv(
                "APP_ENV",
                "development",
            ).strip().lower(),
            log_level=os.getenv(
                "LOG_LEVEL",
                "INFO",
            ).strip().upper(),
        )