"""Emergency recovery of a NEFOR SPOT JSON.GZ snapshot into a NEW EMPTY Neon DB.

This is deliberately NOT a Telegram command. It does not read DATABASE_URL and
will not overwrite an existing database. Never run against the live database.
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from salon_cost_bot.backups import BACKUP_TABLES, FORMAT, _data_hash  # noqa: E402
from salon_cost_bot.database import SCHEMA, SCHEMA_VERSION  # noqa: E402


def load_and_validate_backup(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as source:
        backup = json.load(source)
    if not isinstance(backup, dict) or backup.get("format") != FORMAT:
        raise ValueError("Неверный формат резервной копии NEFOR SPOT.")
    if backup.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Версия схемы архива отличается от версии приложения.")
    tables = backup.get("tables")
    if not isinstance(tables, dict) or set(tables) != set(BACKUP_TABLES):
        raise ValueError("Архив не содержит полный набор таблиц.")
    for name in BACKUP_TABLES:
        item = tables[name]
        if not isinstance(item, dict):
            raise ValueError(f"Неверный раздел {name}.")
        rows, columns = item.get("rows"), item.get("columns")
        if (not isinstance(rows, list) or not isinstance(columns, list)
                or not columns or len(columns) != len(set(columns))
                or not all(isinstance(c, str) for c in columns)):
            raise ValueError(f"Повреждён раздел {name}.")
        if item.get("count") != len(rows) or item.get("sha256") != _data_hash(rows):
            raise ValueError(f"Нарушена целостность раздела {name}.")
        if any(not isinstance(row, dict) or set(row) != set(columns) for row in rows):
            raise ValueError(f"Некорректные столбцы в разделе {name}.")
    return backup


def _database_identity(url: str) -> tuple:
    parts = urlsplit(url)
    return (parts.hostname, parts.port or 5432, parts.path, parts.username)


async def restore_into_empty_database(backup: dict, target_url: str) -> None:
    conn = await asyncpg.connect(target_url, command_timeout=60)
    try:
        async with conn.transaction():
            await conn.execute(SCHEMA)
            present = {
                row["table_name"]
                for row in await conn.fetch(
                    """SELECT table_name FROM information_schema.tables
                       WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"""
                )
            }
            if present != set(BACKUP_TABLES):
                raise RuntimeError("Целевая база имеет другую структуру таблиц.")
            # No DELETE, DROP or TRUNCATE in the recovery process.
            # Refuse to start if ANY table is populated.
            for table in BACKUP_TABLES:
                existing = await conn.fetchval(f'SELECT COUNT(*) FROM "public"."{table}"')
                if existing:
                    raise RuntimeError(
                        f"База не пустая ({table}: {existing} записей). "
                        "Восстановление остановлено без изменения данных."
                    )
                actual_columns = [
                    row["column_name"]
                    for row in await conn.fetch(
                        """SELECT column_name FROM information_schema.columns
                           WHERE table_schema = 'public' AND table_name = $1
                           ORDER BY ordinal_position""",
                        table,
                    )
                ]
                if actual_columns != backup["tables"][table]["columns"]:
                    raise RuntimeError(f"Структура таблицы {table} не совпадает с архивом.")

            for table in BACKUP_TABLES:
                section = backup["tables"][table]
                rows = section["rows"]
                if not rows:
                    continue
                columns = section["columns"]  # already compared with current DB schema
                names = ", ".join('"' + column + '"' for column in columns)
                placeholders = ", ".join(f"${i}" for i in range(1, len(columns) + 1))
                await conn.executemany(
                    f'INSERT INTO "public"."{table}" ({names}) VALUES ({placeholders})',
                    [tuple(row[column] for column in columns) for row in rows],
                )

            for table in BACKUP_TABLES:
                if table == "settings":
                    continue  # setting_key is a text primary key, no sequence
                await conn.fetchval(
                    f"SELECT setval(pg_get_serial_sequence('public.{table}', 'id'), "
                    f'COALESCE(MAX(id), 1), COUNT(*) > 0) FROM "public"."{table}"'
                )
        print("OK: Резервная копия восстановлена в новую пустую базу Neon.")
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Recover a NEFOR SPOT Neon backup")
    parser.add_argument("backup", type=Path, help="Path to nefor-neon-backup-*.json.gz")
    parser.add_argument("--confirm-empty-target", action="store_true",
                        help="Allow insertion into a new, empty database only")
    args = parser.parse_args()
    # Validate the entire source archive before even opening a DB connection.
    archive = load_and_validate_backup(args.backup)
    target_url = os.environ.get("NEON_RESTORE_DATABASE_URL", "").strip()
    if not args.confirm_empty_target:
        parser.error("Нужен --confirm-empty-target. Восстанавливайте только в НОВУЮ базу.")
    if not target_url:
        parser.error("Задайте NEON_RESTORE_DATABASE_URL для новой пустой базы Neon.")
    for live_name in ("DATABASE_URL", "NEON_DATABASE_URL"):
        live_url = os.environ.get(live_name, "").strip()
        if live_url and _database_identity(live_url) == _database_identity(target_url):
            parser.error(f"Адрес совпадает с {live_name}: восстановление в рабочую базу запрещено.")
    asyncio.run(restore_into_empty_database(archive, target_url))


if __name__ == "__main__":
    main()
