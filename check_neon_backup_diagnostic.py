"""NEFOR SPOT: diagnostics for a read-only Neon backup on Windows.

This script connects once (no pool startup), runs a simple read-only query,
creates the application's JSON.GZ export and verifies its checksums.
It does not run the Telegram bot, create tables or write to Neon.
"""

from __future__ import annotations

import asyncio
import getpass
import gzip
import json
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg

from salon_cost_bot.backups import (
    BACKUP_TABLES,
    FORMAT,
    _data_hash,
    create_database_backup,
    timestamp,
)


class _ConnectionPoolForTest:
    """Expose a single, already connected asyncpg connection as a pool."""

    def __init__(self, connection):
        self.connection = connection

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


class _DatabaseForTest:
    def __init__(self, connection):
        self.pool = _ConnectionPoolForTest(connection)


async def main():
    print("NEFOR SPOT — проверка Neon, только чтение", flush=True)
    url = getpass.getpass("Строка подключения к ветке dev-backup (ввод скрыт): ").strip()
    if not url.startswith(("postgresql://", "postgres://")):
        print("Неправильный формат: строка должна начинаться с postgresql://")
        return

    connection = None
    stage = "подключение к Neon"
    try:
        print("1/4 Подключаюсь к Neon (не более 20 секунд)...", flush=True)
        connection = await asyncio.wait_for(
            asyncpg.connect(url, timeout=15, command_timeout=15),
            timeout=20,
        )

        stage = "простой запрос SELECT"
        print("2/4 Соединение установлено. Проверяю SELECT 1...", flush=True)
        result = await asyncio.wait_for(connection.fetchval("SELECT 1"), timeout=15)
        if result != 1:
            raise RuntimeError("Ответ SELECT 1 неправильный")

        stage = "создание архива"
        print("3/4 SQL работает. Создаю архив (только чтение)...", flush=True)
        destination = Path("backups") / f"nefor-neon-TEST-{timestamp()}.json.gz"
        await asyncio.wait_for(
            create_database_backup(_DatabaseForTest(connection), destination),
            timeout=60,
        )

        stage = "проверка архива"
        print("4/4 Проверяю архив и контрольные суммы...", flush=True)
        with gzip.open(destination, "rt", encoding="utf-8") as stream:
            data = json.load(stream)
        if data.get("format") != FORMAT or set(data.get("tables", {})) != set(BACKUP_TABLES):
            raise RuntimeError("Неполный состав таблиц или неверный формат")
        for name, table_data in data["tables"].items():
            if table_data["count"] != len(table_data["rows"]):
                raise RuntimeError(f"Неверное число строк в {name}")
            if table_data["sha256"] != _data_hash(table_data["rows"]):
                raise RuntimeError(f"Неверная контрольная сумма {name}")

        print("OK — резервный архив создан и проверен.", flush=True)
        for name in ("products", "inventory_movements", "calculations"):
            print(f"{name}: {data['tables'][name]['count']}")
        print(f"Файл: {destination.resolve()}")
    except Exception as error:
        # Do not print raw exception details: connection errors can include secrets.
        print(f"ОШИБКА на этапе «{stage}»: {type(error).__name__}", flush=True)
        if stage == "подключение к Neon":
            print("Порт доступен, но сессия PostgreSQL не открылась. Больше не повторяйте тест: потребуется проверка соединения другим способом.")
        elif stage == "создание архива":
            print("Подключение работает; ошибка внутри процедуры резервного копирования.")
        else:
            print("Присылайте только последнюю строку и название этапа — без пароля.")
    finally:
        if connection is not None:
            try:
                await asyncio.wait_for(connection.close(), timeout=5)
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(main())
