from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import asyncpg

from salon_cost_bot.catalog import BRAND_RATES, CATEGORIES, PRODUCTS

logger = logging.getLogger(__name__)
SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    id BIGSERIAL PRIMARY KEY,
    category_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    visible_to_masters INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS brands (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS products (
    id BIGSERIAL PRIMARY KEY,
    sku TEXT NOT NULL UNIQUE,
    category_id BIGINT NOT NULL REFERENCES categories(id),
    brand_id BIGINT NOT NULL REFERENCES brands(id),
    name TEXT NOT NULL,
    unit TEXT NOT NULL DEFAULT 'g'
        CHECK (unit IN ('g', 'ml', 'pcs')),
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
    id BIGSERIAL PRIMARY KEY,
    rule_key TEXT NOT NULL UNIQUE,
    category_id BIGINT NOT NULL REFERENCES categories(id),
    brand_id BIGINT NOT NULL REFERENCES brands(id),
    product_id BIGINT REFERENCES products(id),
    calculation_rate TEXT NOT NULL,
    changed_by BIGINT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pricing_lookup
    ON pricing_rules(category_id, brand_id, product_id);

CREATE TABLE IF NOT EXISTS price_history (
    id BIGSERIAL PRIMARY KEY,
    pricing_rule_id BIGINT NOT NULL REFERENCES pricing_rules(id),
    old_rate TEXT NOT NULL,
    new_rate TEXT NOT NULL,
    changed_by BIGINT NOT NULL,
    changed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calculations (
    id BIGSERIAL PRIMARY KEY,
    completion_token TEXT NOT NULL UNIQUE,
    telegram_user_id BIGINT NOT NULL,
    username TEXT,
    full_name TEXT NOT NULL,
    total_cost TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_calculations_user_created
    ON calculations(telegram_user_id, created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS calculation_items (
    id BIGSERIAL PRIMARY KEY,
    calculation_id BIGINT NOT NULL REFERENCES calculations(id),
    product_id BIGINT NOT NULL REFERENCES products(id),
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

CREATE TABLE IF NOT EXISTS inventory_sessions (
    id BIGSERIAL PRIMARY KEY,
    started_by BIGINT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('active', 'completed', 'cancelled')),
    started_at TEXT NOT NULL,
    completed_at TEXT,
    checked_count INTEGER NOT NULL DEFAULT 0,
    adjusted_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    total_negative TEXT NOT NULL DEFAULT '0',
    total_positive TEXT NOT NULL DEFAULT '0'
);

CREATE TABLE IF NOT EXISTS inventory_movements (
    id BIGSERIAL PRIMARY KEY,
    product_id BIGINT NOT NULL REFERENCES products(id),
    quantity_delta TEXT NOT NULL,
    unit TEXT NOT NULL,
    balance_before TEXT NOT NULL,
    balance_after TEXT NOT NULL,
    movement_type TEXT NOT NULL CHECK (
        movement_type IN (
            'INITIAL_BALANCE',
            'RECEIPT',
            'CALCULATION_USAGE',
            'WRITE_OFF',
            'INVENTORY_ADJUSTMENT',
            'CORRECTION'
        )
    ),
    calculation_id BIGINT REFERENCES calculations(id),
    inventory_session_id BIGINT REFERENCES inventory_sessions(id),
    telegram_user_id BIGINT,
    admin_user_id BIGINT,
    purchase_price_per_package TEXT,
    note TEXT,
    idempotency_key TEXT UNIQUE,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_movements_product_created
    ON inventory_movements(product_id, created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_movements_created
    ON inventory_movements(created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS inventory_session_items (
    id BIGSERIAL PRIMARY KEY,
    session_id BIGINT NOT NULL REFERENCES inventory_sessions(id),
    product_id BIGINT NOT NULL REFERENCES products(id),
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


class CursorResult:
    def __init__(
        self,
        *,
        row: asyncpg.Record | None = None,
        rows: list[asyncpg.Record] | None = None,
        lastrowid: int | None = None,
        rowcount: int = 0,
    ) -> None:
        self._row = row
        self._rows = rows or []
        self.lastrowid = lastrowid
        self.rowcount = rowcount

    async def fetchone(self):
        if self._row is not None:
            return self._row
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        if self._rows:
            return self._rows
        return [self._row] if self._row is not None else []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _postgres_sql(sql: str) -> str:
    # Existing service code uses SQLite-style ? placeholders.
    # Convert them to PostgreSQL $1, $2, ...
    result: list[str] = []
    index = 1
    in_single = False
    in_double = False

    for char in sql:
        if char == "'" and not in_double:
            in_single = not in_single
            result.append(char)
        elif char == '"' and not in_single:
            in_double = not in_double
            result.append(char)
        elif char == "?" and not in_single and not in_double:
            result.append(f"${index}")
            index += 1
        else:
            result.append(char)

    converted = "".join(result)

    # PostgreSQL does not support SQLite's COLLATE NOCASE.
    converted = converted.replace(" COLLATE NOCASE", "")
    converted = converted.replace(" collate nocase", "")

    # Preserve case-insensitive product search.
    converted = converted.replace("p.name LIKE ", "p.name ILIKE ")
    converted = converted.replace("b.name LIKE ", "b.name ILIKE ")
    converted = converted.replace("c.name LIKE ", "c.name ILIKE ")

    return converted


def _is_insert(sql: str) -> bool:
    return sql.lstrip().upper().startswith("INSERT INTO")


def _table_from_insert(sql: str) -> str | None:
    cleaned = sql.replace("\n", " ").replace("\t", " ")
    parts = cleaned.split()

    try:
        index = [p.upper() for p in parts].index("INTO")
    except ValueError:
        return None

    if index + 1 >= len(parts):
        return None

    return parts[index + 1].strip('"')


class ConnectionAdapter:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def execute(
        self,
        sql: str,
        parameters: tuple[Any, ...] = (),
    ) -> CursorResult:
        converted = _postgres_sql(sql)
        table = _table_from_insert(converted) if _is_insert(converted) else None

        # Existing services rely on cursor.lastrowid after INSERT.
        # PostgreSQL uses RETURNING id instead.
        if table in {
            "categories",
            "brands",
            "products",
            "pricing_rules",
            "price_history",
            "calculations",
            "calculation_items",
            "inventory_movements",
            "inventory_sessions",
            "inventory_session_items",
        }:
            upper = converted.upper()

            # ON CONFLICT ... DO NOTHING may legitimately insert nothing.
            if "RETURNING" not in upper:
                converted = converted.rstrip().rstrip(";") + " RETURNING id"

            row = await self._conn.fetchrow(converted, *parameters)

            if row is None:
                return CursorResult(lastrowid=None, rowcount=0)

            return CursorResult(
                row=row,
                lastrowid=int(row["id"]),
                rowcount=1,
            )

        status = await self._conn.execute(converted, *parameters)
        rowcount = 0

        try:
            rowcount = int(status.rsplit(" ", 1)[-1])
        except (ValueError, IndexError):
            pass

        return CursorResult(rowcount=rowcount)

    def execute_cursor(
        self,
        sql: str,
        parameters: tuple[Any, ...] = (),
    ):
        return _CursorContext(self._conn, sql, parameters)

    async def fetchone(
        self,
        sql: str,
        parameters: tuple[Any, ...] = (),
    ):
        return await self._conn.fetchrow(
            _postgres_sql(sql),
            *parameters,
        )

    async def fetchall(
        self,
        sql: str,
        parameters: tuple[Any, ...] = (),
    ):
        return list(
            await self._conn.fetch(
                _postgres_sql(sql),
                *parameters,
            )
        )


class _CursorContext:
    def __init__(
        self,
        conn: asyncpg.Connection,
        sql: str,
        parameters: tuple[Any, ...],
    ) -> None:
        self.conn = conn
        self.sql = sql
        self.parameters = parameters
        self.result: CursorResult | None = None

    async def __aenter__(self):
        converted = _postgres_sql(self.sql)
        rows = list(await self.conn.fetch(converted, *self.parameters))
        self.result = CursorResult(rows=rows)
        return self.result

    async def __aexit__(self, exc_type, exc, tb):
        return False


class TransactionConnection(ConnectionAdapter):
    def execute(
        self,
        sql: str,
        parameters: tuple[Any, ...] = (),
    ):
        # services.py uses BOTH:
        #   await conn.execute(...)
        # and:
        #   async with conn.execute(...) as cursor
        return _ExecuteProxy(self._conn, sql, parameters)


class _ExecuteProxy:
    def __init__(
        self,
        conn: asyncpg.Connection,
        sql: str,
        parameters: tuple[Any, ...],
    ) -> None:
        self.conn = conn
        self.sql = sql
        self.parameters = parameters
        self._result: CursorResult | None = None

    async def _run(self) -> CursorResult:
        if self._result is None:
            adapter = ConnectionAdapter(self.conn)

            converted = _postgres_sql(self.sql)

            # SELECT statements need rows, not an execute status.
            if converted.lstrip().upper().startswith(("SELECT", "WITH")):
                rows = list(
                    await self.conn.fetch(
                        converted,
                        *self.parameters,
                    )
                )
                self._result = CursorResult(rows=rows)
            else:
                self._result = await adapter.execute(
                    self.sql,
                    self.parameters,
                )

        return self._result

    def __await__(self):
        return self._run().__await__()

    async def __aenter__(self):
        return await self._run()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class Database:
    """PostgreSQL/Neon database adapter."""

    def __init__(self, database_url: str) -> None:
        if not database_url:
            raise RuntimeError("Не задан DATABASE_URL для Neon.")
        self.database_url = database_url
        self.pool: asyncpg.Pool | None = None

    async def open(self) -> None:
        self.pool = await asyncpg.create_pool(
            self.database_url,
            min_size=1,
            max_size=5,
            command_timeout=30,
        )

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(SCHEMA)

        await self.seed_initial_data()

        logger.info(
            "PostgreSQL/Neon database ready; schema version=%s",
            SCHEMA_VERSION,
        )

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[TransactionConnection]:
        if self.pool is None:
            raise RuntimeError("Database is not open.")

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                yield TransactionConnection(conn)

    async def fetchone(
        self,
        sql: str,
        parameters: tuple[Any, ...] = (),
    ):
        if self.pool is None:
            raise RuntimeError("Database is not open.")

        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                _postgres_sql(sql),
                *parameters,
            )

    async def fetchall(
        self,
        sql: str,
        parameters: tuple[Any, ...] = (),
    ):
        if self.pool is None:
            raise RuntimeError("Database is not open.")

        async with self.pool.acquire() as conn:
            return list(
                await conn.fetch(
                    _postgres_sql(sql),
                    *parameters,
                )
            )

    async def seed_initial_data(self) -> None:
        now = utc_now()

        async with self.transaction() as conn:
            for key, name, master_visible, sort_order in CATEGORIES:
                await conn.execute(
                    """
                    INSERT INTO categories
                        (category_key, name, visible_to_masters, sort_order)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(category_key) DO NOTHING
                    """,
                    (
                        key,
                        name,
                        int(master_visible),
                        sort_order,
                    ),
                )

            category_rows = await self._fetchall_on(
                conn,
                "SELECT id, category_key FROM categories",
            )
            category_ids = {
                row["category_key"]: row["id"]
                for row in category_rows
            }

            for item in PRODUCTS:
                await conn.execute(
                    """
                    INSERT INTO brands(name)
                    VALUES (?)
                    ON CONFLICT(name) DO NOTHING
                    """,
                    (item["brand"],),
                )

            brand_rows = await self._fetchall_on(
                conn,
                "SELECT id, name FROM brands",
            )
            brand_ids = {
                row["name"]: row["id"]
                for row in brand_rows
            }

            for category_key, brand, rate in BRAND_RATES:
                rule_key = (
                    f"brand:{category_key}:{brand.casefold()}"
                )

                await conn.execute(
                    """
                    INSERT INTO pricing_rules
                        (
                            rule_key,
                            category_id,
                            brand_id,
                            product_id,
                            calculation_rate,
                            updated_at
                        )
                    VALUES (?, ?, ?, NULL, ?, ?)
                    ON CONFLICT(rule_key) DO NOTHING
                    """,
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
                    """
                    INSERT INTO products
                        (
                            sku,
                            category_id,
                            brand_id,
                            name,
                            unit,
                            purchase_price_per_unit,
                            current_stock,
                            visible_to_masters,
                            needs_inventory,
                            created_at,
                            updated_at
                        )
                    VALUES (?, ?, ?, ?, ?, ?, '0', ?, ?, ?, ?)
                    ON CONFLICT(sku) DO NOTHING
                    """,
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
                    """
                    UPDATE products
                    SET current_stock = ?
                    WHERE id = ?
                    """,
                    (
                        starting_balance,
                        product_id,
                    ),
                )

                if starting_balance != "0":
                    await conn.execute(
                        """
                        INSERT INTO inventory_movements
                            (
                                product_id,
                                quantity_delta,
                                unit,
                                balance_before,
                                balance_after,
                                movement_type,
                                note,
                                created_at
                            )
                        VALUES (
                            ?, ?, ?, '0', ?,
                            'INITIAL_BALANCE',
                            'Импорт исторического начального остатка',
                            ?
                        )
                        """,
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
        conn: TransactionConnection,
        sql: str,
        parameters: tuple[Any, ...] = (),
    ):
        cursor = await conn.execute(sql, parameters)
        return await cursor.fetchall()

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None