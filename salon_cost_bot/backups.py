"""Private Neon/PostgreSQL data snapshots and CSV exports for NEFOR SPOT.

The .json.gz snapshot is a logical data backup for the versioned application
schema, NOT a pg_dump archive. To recover it use scripts/restore_neon_backup.py
on a NEW, EMPTY Neon database with the matching schema version.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from salon_cost_bot.database import Database, SCHEMA_VERSION

FORMAT = "nefor-spot-postgres-json-v1"
# Parent tables before dependants; settings has a text PK.
BACKUP_TABLES = (
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
)


def timestamp() -> str:
    return datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")


def _data_hash(rows: list[dict[str, Any]]) -> str:
    raw = json.dumps(rows, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def create_database_backup(db: Database, destination: Path) -> Path:
    """Create a consistent, recoverable logical snapshot of the current Neon data.

    No SQLite files or PostgreSQL command-line tools are required. An unfamiliar
    additional table causes a hard failure, not an incomplete 'full backup'.
    """
    if db.pool is None:
        raise RuntimeError("Соединение с Neon не открыто.")

    async with db.pool.acquire() as conn:
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            visible_tables = {
                row["table_name"]
                for row in await conn.fetch(
                    """SELECT table_name FROM information_schema.tables
                       WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"""
                )
            }
            expected = set(BACKUP_TABLES)
            if visible_tables != expected:
                raise RuntimeError(
                    "Структура Neon изменилась. Резервная копия не создана: "
                    f"неожиданные таблицы={sorted(visible_tables - expected)}, "
                    f"отсутствуют={sorted(expected - visible_tables)}."
                )

            payload: dict[str, Any] = {
                "format": FORMAT,
                "schema_version": SCHEMA_VERSION,
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "tables": {},
            }
            for table in BACKUP_TABLES:
                columns = [
                    row["column_name"]
                    for row in await conn.fetch(
                        """SELECT column_name FROM information_schema.columns
                           WHERE table_schema = 'public' AND table_name = $1
                           ORDER BY ordinal_position""",
                        table,
                    )
                ]
                primary_key = "setting_key" if table == "settings" else "id"
                records = await conn.fetch(
                    f'SELECT * FROM "public"."{table}" ORDER BY "{primary_key}"'
                )
                rows = [dict(record) for record in records]
                payload["tables"][table] = {
                    "columns": columns,
                    "count": len(rows),
                    "sha256": _data_hash(rows),
                    "rows": rows,
                }

    destination.parent.mkdir(parents=True, exist_ok=True)
    # Only persist the complete archive, never a partially written file.
    temporary = destination.with_name(destination.name + ".tmp")
    try:
        with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=6) as output:
            json.dump(payload, output, ensure_ascii=False, separators=(",", ":"))
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


async def export_csv(db: Database, kind: str, destination: Path) -> Path:
    queries = {
        "inventory": """SELECT p.id, c.name AS category, b.name AS brand, p.name,
                               p.unit, p.current_stock, p.purchase_price_per_unit,
                               p.purchase_price_per_package, p.package_quantity,
                               p.visible_to_masters, p.needs_inventory, p.is_active
                        FROM products p JOIN categories c ON c.id = p.category_id
                        JOIN brands b ON b.id = p.brand_id
                        ORDER BY c.sort_order, b.name, p.name""",
        "movements": """SELECT m.id, p.name AS product, m.movement_type,
                                 m.quantity_delta, m.unit, m.balance_before,
                                 m.balance_after, m.calculation_id,
                                 m.inventory_session_id, m.telegram_user_id,
                                 m.admin_user_id, m.note, m.created_at
                          FROM inventory_movements m JOIN products p ON p.id = m.product_id
                          ORDER BY m.id""",
        "calculations": """SELECT c.id, c.telegram_user_id, c.username,
                                    c.full_name, c.total_cost, c.created_at,
                                    i.product_name_snapshot, i.brand_snapshot,
                                    i.category_snapshot, i.quantity, i.unit,
                                    i.calculation_rate_snapshot, i.line_cost
                             FROM calculations c
                             JOIN calculation_items i ON i.calculation_id = c.id
                             ORDER BY c.id, i.id""",
    }
    if kind not in queries:
        raise ValueError("Неизвестный тип экспорта.")
    rows = await db.fetchall(queries[kind])
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8-sig") as output:
        if rows:
            writer = csv.writer(output, delimiter=";")
            writer.writerow(rows[0].keys())
            for row in rows:
                writer.writerow([row[key] for key in row.keys()])
    return destination
