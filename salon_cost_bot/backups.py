from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

import aiosqlite

from salon_cost_bot.database import Database


def timestamp() -> str:
    return datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")


async def create_database_backup(db: Database, destination: Path) -> Path:
    """Create an online SQLite backup through SQLite's backup API."""
    if db.conn is None:
        raise RuntimeError("База данных не открыта.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    async with db._lock:
        target = await aiosqlite.connect(destination.as_posix())
        try:
            await db.conn.backup(target)
        finally:
            await target.close()
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
