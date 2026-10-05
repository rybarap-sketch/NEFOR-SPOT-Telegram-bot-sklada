import asyncio
import os
import sqlite3
from pathlib import Path

import asyncpg


SQLITE_PATH = Path("data/salon_costs.sqlite3")

TABLES = [
    "categories",
    "brands",
    "products",
    "pricing_rules",
    "price_history",
    "calculations",
    "calculation_items",
    "inventory_sessions",
    "inventory_movements",
    "inventory_session_items",
    "settings",
]


async def main():
    database_url = os.getenv("NEON_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("Не найден секрет NEON_DATABASE_URL")

    if not SQLITE_PATH.exists():
        raise RuntimeError(
            f"Не найден файл старой базы: {SQLITE_PATH}"
        )

    sqlite = sqlite3.connect(SQLITE_PATH)
    sqlite.row_factory = sqlite3.Row

    pg = await asyncpg.connect(database_url)

    try:
        async with pg.transaction():
            # На время импорта очищаем созданные ботом стартовые данные.
            await pg.execute("""
                TRUNCATE TABLE
                    inventory_session_items,
                    inventory_movements,
                    calculation_items,
                    calculations,
                    price_history,
                    pricing_rules,
                    inventory_sessions,
                    products,
                    brands,
                    categories,
                    settings
                RESTART IDENTITY CASCADE
            """)

            for table in TABLES:
                rows = sqlite.execute(
                    f'SELECT * FROM "{table}"'
                ).fetchall()

                if not rows:
                    print(f"{table}: 0")
                    continue

                columns = rows[0].keys()

                column_sql = ", ".join(
                    f'"{column}"' for column in columns
                )

                placeholders = ", ".join(
                    f"${index}"
                    for index in range(1, len(columns) + 1)
                )

                sql = (
                    f'INSERT INTO "{table}" '
                    f'({column_sql}) VALUES ({placeholders})'
                )

                for row in rows:
                    values = [
                        row[column]
                        for column in columns
                    ]
                    await pg.execute(sql, *values)

                print(f"{table}: {len(rows)}")

            # После импорта синхронизируем PostgreSQL sequences
            # с сохранёнными SQLite ID.
            sequence_tables = [
                "categories",
                "brands",
                "products",
                "pricing_rules",
                "price_history",
                "calculations",
                "calculation_items",
                "inventory_sessions",
                "inventory_movements",
                "inventory_session_items",
            ]

            for table in sequence_tables:
                await pg.execute(
                    f"""
                    SELECT setval(
                        pg_get_serial_sequence('{table}', 'id'),
                        COALESCE((SELECT MAX(id) FROM {table}), 1),
                        (SELECT COUNT(*) > 0 FROM {table})
                    )
                    """
                )

        print("")
        print("================================")
        print("МИГРАЦИЯ УСПЕШНО ЗАВЕРШЕНА")
        print("================================")

        # Контроль
        for table in TABLES:
            sqlite_count = sqlite.execute(
                f'SELECT COUNT(*) FROM "{table}"'
            ).fetchone()[0]

            neon_count = await pg.fetchval(
                f'SELECT COUNT(*) FROM "{table}"'
            )

            status = (
                "OK"
                if sqlite_count == neon_count
                else "ОШИБКА"
            )

            print(
                f"{table}: "
                f"SQLite={sqlite_count}, "
                f"Neon={neon_count} "
                f"[{status}]"
            )

    finally:
        sqlite.close()
        await pg.close()


if __name__ == "__main__":
    asyncio.run(main())