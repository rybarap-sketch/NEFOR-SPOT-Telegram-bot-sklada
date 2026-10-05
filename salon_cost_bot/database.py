from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

import aiosqlite

from salon_cost_bot.catalog import BRAND_RATES, CATEGORIES, PRODUCTS

logger = logging.getLogger(__name__)
SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY,
    category_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    visible_to_masters INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS brands (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    is_active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    sku TEXT NOT NULL UNIQUE,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    brand_id INTEGER NOT NULL REFERENCES brands(id),
    name TEXT NOT NULL,
    unit TEXT NOT NULL DEFAULT 'g' CHECK (unit IN ('g', 'ml', 'pcs')),
    package_quantity TEXT,
    purchase_price_per_package TEXT,
    purchase_price_per_unit TEXT,
    current_stock TEXT NOT NULL DEFAULT '0',
    low_stock_threshold TEXT,
    visible_to_masters INTEGER NOT NULL DEFAULT 0,
    needs_inventory INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_products_category_brand_active
    ON products(category_id, brand_id, is_active);
CREATE INDEX IF NOT EXISTS idx_products_master_visible
    ON products(visible_to_masters, is_active);
CREATE TABLE IF NOT EXISTS pricing_rules (
    id INTEGER PRIMARY KEY,
    rule_key TEXT NOT NULL UNIQUE,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    brand_id INTEGER NOT NULL REFERENCES brands(id),
    product_id INTEGER REFERENCES products(id),
    calculation_rate TEXT NOT NULL,
    changed_by INTEGER,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pricing_lookup
    ON pricing_rules(category_id, brand_id, product_id);
CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY,
    pricing_rule_id INTEGER NOT NULL REFERENCES pricing_rules(id),
    old_rate TEXT NOT NULL,
    new_rate TEXT NOT NULL,
    changed_by INTEGER NOT NULL,
    changed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS calculations (
    id INTEGER PRIMARY KEY,
    completion_token TEXT NOT NULL UNIQUE,
    telegram_user_id INTEGER NOT NULL,
    username TEXT,
    full_name TEXT NOT NULL,
    total_cost TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_calculations_user_created
    ON calculations(telegram_user_id, created_at DESC, id DESC);
CREATE TABLE IF NOT EXISTS calculation_items (
    id INTEGER PRIMARY KEY,
    calculation_id INTEGER NOT NULL REFERENCES calculations(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    product_name_snapshot TEXT NOT NULL,
    brand_snapshot TEXT NOT NULL,
    category_snapshot TEXT NOT NULL,
    quantity TEXT NOT NULL,
    unit TEXT NOT NULL,
    calculation_rate_snapshot TEXT NOT NULL,
    line_cost TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_calculation_items_calculation
    ON calculation_items(calculation_id, id);
CREATE TABLE IF NOT EXISTS inventory_movements (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity_delta TEXT NOT NULL,
    unit TEXT NOT NULL,
    balance_before TEXT NOT NULL,
    balance_after TEXT NOT NULL,
    movement_type TEXT NOT NULL CHECK (movement_type IN (
        'INITIAL_BALANCE', 'RECEIPT', 'CALCULATION_USAGE', 'WRITE_OFF',
        'INVENTORY_ADJUSTMENT', 'CORRECTION'
    )),
    calculation_id INTEGER REFERENCES calculations(id),
    inventory_session_id INTEGER,
    telegram_user_id INTEGER,
    admin_user_id INTEGER,
    purchase_price_per_package TEXT,
    note TEXT,
    idempotency_key TEXT UNIQUE,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_movements_product_created
    ON inventory_movements(product_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_movements_created
    ON inventory_movements(created_at DESC, id DESC);
CREATE TABLE IF NOT EXISTS inventory_sessions (
    id INTEGER PRIMARY KEY,
    started_by INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'completed', 'cancelled')),
    started_at TEXT NOT NULL,
    completed_at TEXT,
    checked_count INTEGER NOT NULL DEFAULT 0,
    adjusted_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    total_negative TEXT NOT NULL DEFAULT '0',
    total_positive TEXT NOT NULL DEFAULT '0'
);
CREATE TABLE IF NOT EXISTS inventory_session_items (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES inventory_sessions(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    system_balance TEXT NOT NULL,
    actual_balance TEXT,
    quantity_delta TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'checked', 'unchanged', 'skipped')),
    UNIQUE(session_id, product_id)
);
CREATE TABLE IF NOT EXISTS settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    """Serialized async access to a persistent SQLite database."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existed = self.path.exists() and self.path.stat().st_size > 0
        self.conn = await aiosqlite.connect(
            self.path.as_posix(), isolation_level=None
        )
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA foreign_keys = ON")
        await self.conn.execute("PRAGMA journal_mode = WAL")
        await self.conn.execute("PRAGMA busy_timeout = 5000")

        version_row = await self.fetchone("PRAGMA user_version")
        version = int(version_row[0]) if version_row else 0
        if version < SCHEMA_VERSION and existed:
            await self._backup_before_migration()

        async with self.transaction() as conn:
            for statement in SCHEMA.split(";"):
                sql = statement.strip()
                if sql:
                    await conn.execute(sql)
            await conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        await self.seed_initial_data()
        logger.info("Database ready; schema version=%s", SCHEMA_VERSION)

    async def _backup_before_migration(self) -> None:
        if self.conn is None:
            return
        backup_dir = self.path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        destination = backup_dir / f"pre-migration-{timestamp}.sqlite3"
        target = await aiosqlite.connect(destination.as_posix())
        try:
            await self.conn.backup(target)
        finally:
            await target.close()
        logger.info("Created pre-migration database backup")

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        if self.conn is None:
            raise RuntimeError("Database is not open.")
        async with self._lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
                await self.conn.commit()
            except BaseException:
                await self.conn.rollback()
                raise

    async def fetchone(
        self, sql: str, parameters: tuple[Any, ...] = ()
    ) -> aiosqlite.Row | None:
        if self.conn is None:
            raise RuntimeError("Database is not open.")
        async with self._lock:
            async with self.conn.execute(sql, parameters) as cursor:
                return await cursor.fetchone()

    async def fetchall(
        self, sql: str, parameters: tuple[Any, ...] = ()
    ) -> list[aiosqlite.Row]:
        if self.conn is None:
            raise RuntimeError("Database is not open.")
        async with self._lock:
            async with self.conn.execute(sql, parameters) as cursor:
                return await cursor.fetchall()

    async def seed_initial_data(self) -> None:
        if self.conn is None:
            raise RuntimeError("Database is not open.")
        now = utc_now()
        async with self.transaction() as conn:
            for key, name, master_visible, sort_order in CATEGORIES:
                await conn.execute(
                    """INSERT INTO categories
                       (category_key, name, visible_to_masters, sort_order)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(category_key) DO NOTHING""",
                    (key, name, int(master_visible), sort_order),
                )

            category_rows = await self._fetchall_on(
                conn, "SELECT id, category_key FROM categories"
            )
            category_ids = {row["category_key"]: row["id"] for row in category_rows}

            for item in PRODUCTS:
                await conn.execute(
                    "INSERT INTO brands(name) VALUES (?) ON CONFLICT(name) DO NOTHING",
                    (item["brand"],),
                )
            brand_rows = await self._fetchall_on(conn, "SELECT id, name FROM brands")
            brand_ids = {row["name"]: row["id"] for row in brand_rows}

            for category_key, brand, rate in BRAND_RATES:
                rule_key = f"brand:{category_key}:{brand.casefold()}"
                await conn.execute(
                    """INSERT INTO pricing_rules
                       (rule_key, category_id, brand_id, product_id,
                        calculation_rate, updated_at)
                       VALUES (?, ?, ?, NULL, ?, ?)
                       ON CONFLICT(rule_key) DO NOTHING""",
                    (
                        rule_key,
                        category_ids[category_key],
                        brand_ids[brand],
                        rate,
                        now,
                    ),
                )

            for item in PRODUCTS:
                cursor = await conn.execute(
                    """INSERT INTO products
                       (sku, category_id, brand_id, name, unit,
                        purchase_price_per_unit, current_stock,
                        visible_to_masters, needs_inventory, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, '0', ?, ?, ?, ?)
                       ON CONFLICT(sku) DO NOTHING""",
                    (
                        item["sku"],
                        category_ids[item["category"]],
                        brand_ids[item["brand"]],
                        item["name"],
                        item["unit"],
                        item["purchase"],
                        int(bool(item["visible"])),
                        int(bool(item["needs_inventory"])),
                        now,
                        now,
                    ),
                )
                if cursor.rowcount != 1:
                    continue
                product_id = cursor.lastrowid
                starting_balance = str(item["stock"])
                await conn.execute(
                    "UPDATE products SET current_stock = ? WHERE id = ?",
                    (starting_balance, product_id),
                )
                if starting_balance != "0":
                    await conn.execute(
                        """INSERT INTO inventory_movements
                           (product_id, quantity_delta, unit, balance_before,
                            balance_after, movement_type, note, created_at)
                           VALUES (?, ?, ?, '0', ?, 'INITIAL_BALANCE',
                                   'Импорт исторического начального остатка', ?)""",
                        (
                            product_id,
                            starting_balance,
                            item["unit"],
                            starting_balance,
                            now,
                        ),
                    )

    @staticmethod
    async def _fetchall_on(
        conn: aiosqlite.Connection, sql: str
    ) -> list[aiosqlite.Row]:
        async with conn.execute(sql) as cursor:
            return await cursor.fetchall()

    async def close(self) -> None:
        if self.conn is not None:
            await self.conn.close()
            self.conn = None
