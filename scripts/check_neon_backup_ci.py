"""Run the real NEFOR SPOT snapshot code from GitHub Actions against dev-backup.

No Telegram bot starts, no Neon data changes, and no inventory backup is
uploaded to GitHub Actions artifacts. Only table row counts are printed.
"""
from __future__ import annotations

import asyncio
import gzip
import json
import os
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg

# Allow `python scripts/check_neon_backup_ci.py` to import the app package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from salon_cost_bot.backups import (  # noqa: E402
    BACKUP_TABLES,
    FORMAT,
    _data_hash,
    create_database_backup,
)


class _Pool:
    def __init__(self, connection: asyncpg.Connection) -> None:
        self.connection = connection

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


class _ReadOnlyTestDatabase:
    def __init__(self, connection: asyncpg.Connection) -> None:
        self.pool = _Pool(connection)


async def check() -> None:
    url = os.getenv("NEFOR_TEST_NEON_URL", "").strip()
    if not url:
        raise RuntimeError("Не задан GitHub Actions secret NEFOR_TEST_NEON_URL")
    if not url.startswith(("postgresql://", "postgres://")):
        raise ValueError("Некорректная строка подключения Neon (нужен postgresql://)")

    print("1/4 Подключение из GitHub Actions...", flush=True)
    connection = await asyncio.wait_for(
        asyncpg.connect(url, timeout=25, command_timeout=30), timeout=30
    )
    try:
        print("2/4 SELECT 1...", flush=True)
        if await asyncio.wait_for(connection.fetchval("SELECT 1"), timeout=15) != 1:
            raise RuntimeError("Неожиданный результат SELECT 1")
        with tempfile.TemporaryDirectory(prefix="nefor-neon-test-") as tmpdir:
            path = Path(tmpdir) / "nefor-neon-TEST.json.gz"
            print("3/4 Создание архивной копии (SELECT only)...", flush=True)
            await asyncio.wait_for(
                create_database_backup(_ReadOnlyTestDatabase(connection), path),
                timeout=90,
            )
            print("4/4 Проверка состава таблиц и контрольных сумм...", flush=True)
            with gzip.open(path, "rt", encoding="utf-8") as file:
                data = json.load(file)
            if data.get("format") != FORMAT:
                raise RuntimeError("Формат архива не совпадает")
            if set(data.get("tables", {})) != set(BACKUP_TABLES):
                raise RuntimeError("В архиве отсутствуют таблицы")
            for table in BACKUP_TABLES:
                section = data["tables"][table]
                if section["count"] != len(section["rows"]):
                    raise RuntimeError(f"Неверное число строк: {table}")
                if section["sha256"] != _data_hash(section["rows"]):
                    raise RuntimeError(f"Нарушена контрольная сумма: {table}")
            # No artifact upload: archive disappears with the temporary directory.
            for table in ("products", "inventory_movements", "calculations"):
                print(f"{table}: {data['tables'][table]['count']}", flush=True)
            print("OK: архив Neon создан и проверен; данные не изменены.", flush=True)
    finally:
        await connection.close(timeout=5)


if __name__ == "__main__":
    try:
        asyncio.run(check())
    except Exception as exc:
        # Never print exception text or a connection string to CI logs.
        print(f"FAILED: {type(exc).__name__}", flush=True)
        sys.exit(1)
