"""Безопасная локальная проверка резервного копирования NEFOR SPOT.

Только чтение PostgreSQL/Neon. Не запускает Telegram-бота,
не создаёт и не изменяет таблицы базы. Никаких паролей в файле нет.
Запускать из корня проекта с установленным asyncpg.
"""
from __future__ import annotations

import asyncio
import getpass
import gzip
import json
from pathlib import Path

import asyncpg

from salon_cost_bot.backups import (
    BACKUP_TABLES,
    FORMAT,
    _data_hash,
    create_database_backup,
    timestamp,
)
from salon_cost_bot.database import Database


async def main() -> None:
    print("Проверка бэкапа NEFOR SPOT (только чтение базы).")
    print("Вставь строку подключения именно к ветке dev-backup в Neon.")
    url = getpass.getpass("Строка подключения (ввод скрыт): ").strip()
    if not url.startswith(("postgresql://", "postgres://")):
        raise ValueError("Неверный формат строки подключения PostgreSQL.")

    pool = await asyncpg.create_pool(
        url, min_size=1, max_size=1, command_timeout=60
    )
    try:
        db = Database(url)
        # Не вызываем db.open(): там есть инициализация/запись каталога.
        # Используем только read-only транзакцию внутри create_database_backup().
        db.pool = pool
        destination = Path("backups") / f"nefor-neon-TEST-{timestamp()}.json.gz"
        await create_database_backup(db, destination)

        with gzip.open(destination, "rt", encoding="utf-8") as stream:
            data = json.load(stream)
        if data.get("format") != FORMAT or set(data.get("tables", {})) != set(BACKUP_TABLES):
            raise RuntimeError("Неверный формат или неполный перечень таблиц.")
        for name, section in data["tables"].items():
            rows = section["rows"]
            if section["count"] != len(rows) or section["sha256"] != _data_hash(rows):
                raise RuntimeError(f"Нарушена целостность таблицы {name}.")

        print("\nOK: резервный архив создан, проверены количество записей и контрольные суммы.")
        for name in ("products", "inventory_movements", "calculations"):
            print(f"{name}: {data['tables'][name]['count']}")
        print(f"Файл: {destination.resolve()}")
        print("Это локальный тест: рабочий бот и база production не изменялись.")
    finally:
        await pool.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:
        # Исключение может содержать внутренние детали подключения;
        # показываем только тип и безопасное пояснение.
        print(f"\nПроверка остановлена: {type(error).__name__}.")
        print("Пришли скрин ошибки без строки подключения и пароля.")
        raise SystemExit(1) from None
