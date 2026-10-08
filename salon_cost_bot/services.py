from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Sequence
from uuid import uuid4

import aiosqlite

from salon_cost_bot.database import Database, utc_now
from salon_cost_bot.domain import ZERO, decimal_text, money


def _dict(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


@dataclass(frozen=True, slots=True)
class CalculationLine:
    product_id: int
    quantity: Decimal


class SalonService:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def get_product(self, product_id: int) -> dict[str, Any] | None:
        row = await self.db.fetchone(
            """SELECT p.*, c.category_key, c.name AS category_name,
                      b.name AS brand_name
               FROM products p
               JOIN categories c ON c.id = p.category_id
               JOIN brands b ON b.id = p.brand_id
               WHERE p.id = ?""",
            (product_id,),
        )
        return _dict(row)

    async def category_id(self, key: str) -> int | None:
        row = await self.db.fetchone(
            "SELECT id FROM categories WHERE category_key = ? AND is_active = 1",
            (key,),
        )
        return int(row["id"]) if row else None

    async def product_count(
        self, category_id: int, brand_id: int, *, master_only: bool = False
    ) -> int:
        visible = "AND visible_to_masters = 1" if master_only else ""
        row = await self.db.fetchone(
            f"""SELECT COUNT(*) AS count FROM products
                WHERE category_id = ? AND brand_id = ? AND is_active = 1 {visible}""",
            (category_id, brand_id),
        )
        return int(row["count"]) if row else 0

    async def product_picker(
        self, *, offset: int = 0, limit: int = 30
    ) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            """SELECT p.id, p.name, p.is_active, b.name AS brand_name
               FROM products p JOIN brands b ON b.id = p.brand_id
               WHERE p.is_active = 1
               ORDER BY b.name COLLATE NOCASE, p.name COLLATE NOCASE
               LIMIT ? OFFSET ?""",
            (limit, offset),
        )
        return [dict(row) for row in rows]

    async def inventory_brands(self, category_id: int) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            """SELECT b.id, b.name, COUNT(p.id) AS total
               FROM brands b JOIN products p ON p.brand_id = b.id
               WHERE p.category_id = ? AND p.is_active = 1
               GROUP BY b.id ORDER BY b.name""",
            (category_id,),
        )
        return [dict(row) for row in rows]

    async def inventory_products(
        self, category_id: int, brand_id: int
    ) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            """SELECT id, name, current_stock, unit FROM products
               WHERE category_id = ? AND brand_id = ? AND is_active = 1
               ORDER BY name COLLATE NOCASE""",
            (category_id, brand_id),
        )
        return [dict(row) for row in rows]

    async def admin_product_details(self, product_id: int) -> dict[str, Any]:
        product = await self.get_product(product_id)
        if product is None:
            return {"calculation_rate": None, "last_movement_at": None}
        rate = await self.db.fetchone(
            """SELECT calculation_rate FROM pricing_rules
               WHERE category_id = ? AND brand_id = ?
                 AND (product_id = ? OR product_id IS NULL)
               ORDER BY CASE WHEN product_id IS NULL THEN 1 ELSE 0 END LIMIT 1""",
            (product["category_id"], product["brand_id"], product_id),
        )
        movement = await self.db.fetchone(
            """SELECT created_at FROM inventory_movements
               WHERE product_id = ? ORDER BY id DESC LIMIT 1""",
            (product_id,),
        )
        return {
            "calculation_rate": rate["calculation_rate"] if rate else None,
            "last_movement_at": movement["created_at"] if movement else None,
        }

    async def pricing_rules(
        self, *, offset: int = 0, limit: int = 21
    ) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            """SELECT r.id, r.calculation_rate, c.name AS category_name,
                      b.name AS brand_name, p.name AS product_name,
                      COALESCE(r.product_id, (
                          SELECT target.id FROM products target
                          WHERE target.category_id = r.category_id
                            AND target.brand_id = r.brand_id
                            AND target.is_active = 1
                          ORDER BY target.id LIMIT 1
                      )) AS target_product_id,
                      COALESCE(p.unit, (
                          SELECT target.unit FROM products target
                          WHERE target.category_id = r.category_id
                            AND target.brand_id = r.brand_id
                            AND target.is_active = 1
                          ORDER BY target.id LIMIT 1
                      )) AS target_unit
               FROM pricing_rules r
               JOIN categories c ON c.id = r.category_id
               JOIN brands b ON b.id = r.brand_id
               LEFT JOIN products p ON p.id = r.product_id
               ORDER BY c.sort_order, b.name, p.name
               LIMIT ? OFFSET ?""",
            (limit, offset),
        )
        return [dict(row) for row in rows]

    async def create_database_backup(self, destination):
        from salon_cost_bot.backups import create_database_backup

        return await create_database_backup(self.db, destination)

    async def export_csv(self, kind: str, destination):
        from salon_cost_bot.backups import export_csv

        return await export_csv(self.db, kind, destination)

    async def categories(self, *, master_only: bool = False) -> list[dict[str, Any]]:
        where = "AND c.visible_to_masters = 1 AND p.visible_to_masters = 1" if master_only else ""
        return [
            dict(row)
            for row in await self.db.fetchall(
                f"""SELECT c.id, c.category_key, c.name, c.sort_order,
                           COUNT(p.id) AS product_count
                    FROM categories c
                    JOIN products p ON p.category_id = c.id
                    WHERE c.is_active = 1 AND p.is_active = 1 {where}
                    GROUP BY c.id
                    ORDER BY c.sort_order, c.name"""
            )
        ]

    async def brands(
        self, category_id: int, *, master_only: bool = False
    ) -> list[dict[str, Any]]:
        visible = "AND p.visible_to_masters = 1" if master_only else ""
        return [
            dict(row)
            for row in await self.db.fetchall(
                f"""SELECT b.id, b.name, COUNT(p.id) AS product_count
                    FROM brands b
                    JOIN products p ON p.brand_id = b.id
                    WHERE p.category_id = ? AND p.is_active = 1 {visible}
                    GROUP BY b.id ORDER BY b.name COLLATE NOCASE""",
                (category_id,),
            )
        ]

    async def products(
        self,
        category_id: int,
        brand_id: int,
        *,
        master_only: bool = False,
        limit: int = 30,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        visible = "AND p.visible_to_masters = 1" if master_only else ""
        return [
            dict(row)
            for row in await self.db.fetchall(
                f"""SELECT p.id, p.name, p.unit, p.current_stock, p.is_active,
                           c.name AS category_name, c.category_key,
                           b.name AS brand_name
                    FROM products p
                    JOIN categories c ON c.id = p.category_id
                    JOIN brands b ON b.id = p.brand_id
                    WHERE p.category_id = ? AND p.brand_id = ?
                      AND p.is_active = 1 {visible}
                    ORDER BY p.name COLLATE NOCASE
                    LIMIT ? OFFSET ?""",
                (category_id, brand_id, limit, offset),
            )
        ]

    async def effective_rate(
        self, conn: aiosqlite.Connection, product: aiosqlite.Row
    ) -> Decimal:
        async with conn.execute(
            """SELECT calculation_rate FROM pricing_rules
               WHERE category_id = ? AND brand_id = ?
                 AND (product_id = ? OR product_id IS NULL)
               ORDER BY CASE WHEN product_id IS NULL THEN 1 ELSE 0 END
               LIMIT 1""",
            (product["category_id"], product["brand_id"], product["id"]),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            raise ValueError(f"Для материала «{product['name']}» не задана ставка.")
        return Decimal(row["calculation_rate"])

    async def complete_calculation(
        self,
        *,
        completion_token: str,
        telegram_user_id: int,
        username: str | None,
        full_name: str,
        lines: Sequence[CalculationLine],
    ) -> tuple[int, Decimal, list[dict[str, Any]], bool]:
        if not lines:
            raise ValueError("Добавьте хотя бы один материал.")
        if any(not line.quantity.is_finite() or line.quantity <= ZERO for line in lines):
            raise ValueError("Расход материала должен быть положительным числом.")

        async with self.db.transaction() as conn:
            async with conn.execute(
                "SELECT id, total_cost FROM calculations WHERE completion_token = ?",
                (completion_token,),
            ) as cursor:
                existing = await cursor.fetchone()
            if existing is not None:
                stored = await self._calculation_lines(conn, existing["id"])
                return (
                    existing["id"],
                    Decimal(existing["total_cost"]),
                    stored,
                    False,
                )

            prepared: list[dict[str, Any]] = []
            total = ZERO
            for line in lines:
                async with conn.execute(
                    """SELECT p.*, c.name AS category_name, b.name AS brand_name
                       FROM products p
                       JOIN categories c ON c.id = p.category_id
                       JOIN brands b ON b.id = p.brand_id
                       WHERE p.id = ? AND p.is_active = 1 AND p.visible_to_masters = 1""",
                    (line.product_id,),
                ) as cursor:
                    product = await cursor.fetchone()
                if product is None:
                    raise ValueError("Один из материалов больше недоступен. Начните расчёт заново.")
                rate = await self.effective_rate(conn, product)
                line_cost = money(line.quantity * rate)
                total += line_cost
                prepared.append(
                    {
                        "product_id": product["id"],
                        "name": product["name"],
                        "brand": product["brand_name"],
                        "category": product["category_name"],
                        "quantity": line.quantity,
                        "unit": product["unit"],
                        "rate": rate,
                        "cost": line_cost,
                    }
                )

            created_at = utc_now()
            cursor = await conn.execute(
                """INSERT INTO calculations
                   (completion_token, telegram_user_id, username, full_name,
                    total_cost, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    completion_token,
                    telegram_user_id,
                    username,
                    full_name,
                    decimal_text(money(total)),
                    created_at,
                ),
            )
            calculation_id = int(cursor.lastrowid)

            for index, item in enumerate(prepared):
                await conn.execute(
                    """INSERT INTO calculation_items
                       (calculation_id, product_id, product_name_snapshot,
                        brand_snapshot, category_snapshot, quantity, unit,
                        calculation_rate_snapshot, line_cost)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        calculation_id,
                        item["product_id"],
                        item["name"],
                        item["brand"],
                        item["category"],
                        decimal_text(item["quantity"]),
                        item["unit"],
                        decimal_text(item["rate"]),
                        decimal_text(item["cost"]),
                    ),
                )
                async with conn.execute(
                    "SELECT current_stock FROM products WHERE id = ?",
                    (item["product_id"],),
                ) as cursor:
                    current_row = await cursor.fetchone()
                before = Decimal(current_row["current_stock"])
                after = before - item["quantity"]
                await conn.execute(
                    "UPDATE products SET current_stock = ?, updated_at = ? WHERE id = ?",
                    (decimal_text(after), created_at, item["product_id"]),
                )
                if after < ZERO:
                    await conn.execute(
                        "UPDATE products SET needs_inventory = 1 WHERE id = ?",
                        (item["product_id"],),
                    )
                await conn.execute(
                    """INSERT INTO inventory_movements
                       (product_id, quantity_delta, unit, balance_before,
                        balance_after, movement_type, calculation_id,
                        telegram_user_id, note, idempotency_key, created_at)
                       VALUES (?, ?, ?, ?, ?, 'CALCULATION_USAGE', ?, ?, ?,
                               ?, ?)""",
                    (
                        item["product_id"],
                        decimal_text(-item["quantity"]),
                        item["unit"],
                        decimal_text(before),
                        decimal_text(after),
                        calculation_id,
                        telegram_user_id,
                        f"Списание по расчёту #{calculation_id}",
                        f"{completion_token}:{index}",
                        created_at,
                    ),
                )
            return calculation_id, money(total), prepared, True

    @staticmethod
    async def _calculation_lines(
        conn: aiosqlite.Connection, calculation_id: int
    ) -> list[dict[str, Any]]:
        async with conn.execute(
            """SELECT product_id, product_name_snapshot AS name,
                      brand_snapshot AS brand, category_snapshot AS category,
                      quantity, unit, calculation_rate_snapshot AS rate,
                      line_cost AS cost
               FROM calculation_items WHERE calculation_id = ? ORDER BY id""",
            (calculation_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def inventory(
        self, *, offset: int = 0, limit: int = 30, attention_only: bool = False
    ) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            f"""SELECT p.id, p.name, p.unit, p.current_stock, p.needs_inventory,
                       p.is_active, p.visible_to_masters, p.purchase_price_per_unit,
                       p.purchase_price_per_package, p.package_quantity,
                       p.low_stock_threshold, c.name AS category_name,
                       b.name AS brand_name
                FROM products p
                JOIN categories c ON c.id = p.category_id
                JOIN brands b ON b.id = p.brand_id
                WHERE p.is_active = 1
                ORDER BY c.sort_order, c.name, b.name, p.name
                """
        )
        if attention_only:
            rows = [
                row
                for row in rows
                if (
                    Decimal(row["current_stock"]) < ZERO
                    or row["needs_inventory"]
                    or (
                        row["low_stock_threshold"] is not None
                        and Decimal(row["current_stock"])
                        <= Decimal(row["low_stock_threshold"])
                    )
                )
            ]
        rows = rows[offset : offset + limit]
        result = []
        for row in rows:
            product = dict(row)
            rate_row = await self.db.fetchone(
                """SELECT calculation_rate FROM pricing_rules
                   WHERE category_id = (SELECT category_id FROM products WHERE id = ?)
                     AND brand_id = (SELECT brand_id FROM products WHERE id = ?)
                     AND (product_id = ? OR product_id IS NULL)
                   ORDER BY CASE WHEN product_id IS NULL THEN 1 ELSE 0 END LIMIT 1""",
                (product["id"], product["id"], product["id"]),
            )
            product["calculation_rate"] = (
                rate_row["calculation_rate"] if rate_row else None
            )
            stock = Decimal(product["current_stock"])
            threshold = product["low_stock_threshold"]
            product["below_low_stock_threshold"] = (
                threshold is not None and stock <= Decimal(threshold)
            )
            purchase_rate = product["purchase_price_per_unit"]
            product["estimated_value"] = (
                decimal_text(money(stock * Decimal(purchase_rate)))
                if purchase_rate is not None
                else None
            )
            result.append(product)
        return result

    async def receipt(
        self,
        *,
        product_id: int,
        quantity: Decimal,
        admin_user_id: int,
        note: str | None = None,
        package_quantity: Decimal | None = None,
        package_count: Decimal | None = None,
        price_per_package: Decimal | None = None,
    ) -> tuple[Decimal, Decimal]:
        if not quantity.is_finite() or quantity <= ZERO:
            raise ValueError("Количество прихода должно быть больше нуля.")
        if price_per_package is not None and (not price_per_package.is_finite() or price_per_package < ZERO):
            raise ValueError("Закупочная цена не может быть отрицательной.")
        if package_quantity is not None and (not package_quantity.is_finite() or package_quantity <= ZERO):
            raise ValueError("Масса упаковки должна быть больше нуля.")
        if package_count is not None and (not package_count.is_finite() or package_count <= ZERO):
            raise ValueError("Количество упаковок должно быть больше нуля.")
        now = utc_now()
        async with self.db.transaction() as conn:
            async with conn.execute(
                "SELECT current_stock, unit FROM products WHERE id = ? AND is_active = 1",
                (product_id,),
            ) as cursor:
                product = await cursor.fetchone()
            if product is None:
                raise ValueError("Материал не найден или архивирован.")
            before = Decimal(product["current_stock"])
            after = before + quantity
            await conn.execute(
                """UPDATE products SET current_stock = ?, updated_at = ?,
                   package_quantity = COALESCE(?, package_quantity),
                   purchase_price_per_package = COALESCE(?, purchase_price_per_package),
                   purchase_price_per_unit = COALESCE(?, purchase_price_per_unit)
                   WHERE id = ?""",
                (
                    decimal_text(after),
                    now,
                    decimal_text(package_quantity) if package_quantity else None,
                    decimal_text(price_per_package) if price_per_package else None,
                    decimal_text(price_per_package / package_quantity)
                    if price_per_package and package_quantity
                    else None,
                    product_id,
                ),
            )
            receipt_note = note
            if package_count and package_quantity:
                receipt_note = (
                    f"{decimal_text(package_count)} уп. × "
                    f"{decimal_text(package_quantity)} {product['unit']}"
                    + (f"; {note}" if note else "")
                )
            await conn.execute(
                """INSERT INTO inventory_movements
                   (product_id, quantity_delta, unit, balance_before,
                    balance_after, movement_type, admin_user_id,
                    purchase_price_per_package, note, created_at)
                   VALUES (?, ?, ?, ?, ?, 'RECEIPT', ?, ?, ?, ?)""",
                (
                    product_id,
                    decimal_text(quantity),
                    product["unit"],
                    decimal_text(before),
                    decimal_text(after),
                    admin_user_id,
                    decimal_text(price_per_package) if price_per_package else None,
                    receipt_note,
                    now,
                ),
            )
            return before, after

    async def write_off(
        self,
        *,
        product_id: int,
        quantity: Decimal,
        reason: str,
        admin_user_id: int,
    ) -> tuple[Decimal, Decimal]:
        if not quantity.is_finite() or quantity <= ZERO:
            raise ValueError("Количество списания должно быть больше нуля.")
        now = utc_now()
        async with self.db.transaction() as conn:
            async with conn.execute(
                "SELECT current_stock, unit FROM products WHERE id = ? AND is_active = 1",
                (product_id,),
            ) as cursor:
                product = await cursor.fetchone()
            if product is None:
                raise ValueError("Материал не найден или архивирован.")
            before = Decimal(product["current_stock"])
            after = before - quantity
            await conn.execute(
                "UPDATE products SET current_stock = ?, updated_at = ?, needs_inventory = ? WHERE id = ?",
                (
                    decimal_text(after),
                    now,
                    int(after < ZERO),
                    product_id,
                ),
            )
            await conn.execute(
                """INSERT INTO inventory_movements
                   (product_id, quantity_delta, unit, balance_before,
                    balance_after, movement_type, admin_user_id, note, created_at)
                   VALUES (?, ?, ?, ?, ?, 'WRITE_OFF', ?, ?, ?)""",
                (
                    product_id,
                    decimal_text(-quantity),
                    product["unit"],
                    decimal_text(before),
                    decimal_text(after),
                    admin_user_id,
                    reason,
                    now,
                ),
            )
            return before, after

    async def adjust_inventory(
        self,
        *,
        product_id: int,
        actual_balance: Decimal,
        admin_user_id: int,
        session_id: int | None = None,
    ) -> tuple[Decimal, Decimal, Decimal]:
        if not actual_balance.is_finite() or actual_balance < ZERO:
            raise ValueError("Фактический остаток не может быть отрицательным.")
        now = utc_now()
        async with self.db.transaction() as conn:
            async with conn.execute(
                "SELECT current_stock, unit FROM products WHERE id = ? AND is_active = 1",
                (product_id,),
            ) as cursor:
                product = await cursor.fetchone()
            if product is None:
                raise ValueError("Материал не найден или архивирован.")
            before = Decimal(product["current_stock"])
            delta = actual_balance - before
            await conn.execute(
                """UPDATE products SET current_stock = ?, updated_at = ?,
                   needs_inventory = ? WHERE id = ?""",
                (
                    decimal_text(actual_balance),
                    now,
                    int(actual_balance < ZERO),
                    product_id,
                ),
            )
            await conn.execute(
                """INSERT INTO inventory_movements
                   (product_id, quantity_delta, unit, balance_before,
                    balance_after, movement_type, inventory_session_id,
                    admin_user_id, note, created_at)
                   VALUES (?, ?, ?, ?, ?, 'INVENTORY_ADJUSTMENT', ?, ?, ?, ?)""",
                (
                    product_id,
                    decimal_text(delta),
                    product["unit"],
                    decimal_text(before),
                    decimal_text(actual_balance),
                    session_id,
                    admin_user_id,
                    "Корректировка по фактической инвентаризации",
                    now,
                ),
            )
            return before, actual_balance, delta

    async def change_price(
        self, product_id: int, new_rate: Decimal, admin_user_id: int
    ) -> tuple[Decimal | None, Decimal]:
        if not new_rate.is_finite() or new_rate < ZERO:
            raise ValueError("Расчётная ставка не может быть отрицательной.")
        now = utc_now()
        async with self.db.transaction() as conn:
            async with conn.execute(
                """SELECT p.category_id, p.brand_id,
                          r.id AS rule_id, r.calculation_rate
                   FROM products p
                   LEFT JOIN pricing_rules r
                     ON r.category_id = p.category_id AND r.brand_id = p.brand_id
                    AND r.product_id = p.id
                   WHERE p.id = ? AND p.is_active = 1""",
                (product_id,),
            ) as cursor:
                product = await cursor.fetchone()
            if product is None:
                raise ValueError("Материал не найден или архивирован.")
            previous = (
                Decimal(product["calculation_rate"])
                if product["calculation_rate"] is not None
                else None
            )
            rule_key = f"product:{product_id}"
            if product["rule_id"] is None:
                cursor = await conn.execute(
                    """INSERT INTO pricing_rules
                       (rule_key, category_id, brand_id, product_id,
                        calculation_rate, changed_by, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        rule_key,
                        product["category_id"],
                        product["brand_id"],
                        product_id,
                        decimal_text(new_rate),
                        admin_user_id,
                        now,
                    ),
                )
                rule_id = int(cursor.lastrowid)
                # Capture the inherited rate before applying a product override
                # so the audit trail records the old effective rate.
                async with conn.execute(
                    """SELECT calculation_rate FROM pricing_rules
                       WHERE category_id = ? AND brand_id = ?
                         AND product_id IS NULL""",
                    (product["category_id"], product["brand_id"]),
                ) as cursor:
                    inherited = await cursor.fetchone()
                previous = Decimal(inherited["calculation_rate"]) if inherited else None
            else:
                rule_id = product["rule_id"]
                await conn.execute(
                    """UPDATE pricing_rules SET calculation_rate = ?,
                       changed_by = ?, updated_at = ? WHERE id = ?""",
                    (decimal_text(new_rate), admin_user_id, now, rule_id),
                )
            await conn.execute(
                """INSERT INTO price_history
                   (pricing_rule_id, old_rate, new_rate, changed_by, changed_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    rule_id,
                    decimal_text(previous) if previous is not None else "0",
                    decimal_text(new_rate),
                    admin_user_id,
                    now,
                ),
            )
            return previous, new_rate

    async def create_product(
        self,
        *,
        category_id: int,
        brand_name: str,
        name: str,
        unit: str,
        package_quantity: Decimal | None,
        purchase_price_per_unit: Decimal | None,
        calculation_rate: Decimal | None,
        initial_stock: Decimal,
        visible_to_masters: bool,
        admin_user_id: int,
    ) -> int:
        brand = brand_name.strip()
        product_name = name.strip()
        if not brand or not product_name:
            raise ValueError("Укажите бренд и название материала.")
        if package_quantity is not None and (not package_quantity.is_finite() or package_quantity <= ZERO):
            raise ValueError("Масса упаковки должна быть больше нуля.")
        if purchase_price_per_unit is not None and (not purchase_price_per_unit.is_finite() or purchase_price_per_unit < ZERO):
            raise ValueError("Закупочная цена не может быть отрицательной.")
        if calculation_rate is not None and (not calculation_rate.is_finite() or calculation_rate < ZERO):
            raise ValueError("Расчётная ставка не может быть отрицательной.")
        if not initial_stock.is_finite():
            raise ValueError("Некорректный начальный остаток.")
        now = utc_now()
        sku = f"custom:{category_id}:{brand.casefold()}:{product_name.casefold()}:{uuid4().hex[:10]}"
        async with self.db.transaction() as conn:
            async with conn.execute(
                "SELECT id FROM categories WHERE id = ? AND is_active = 1",
                (category_id,),
            ) as cursor:
                category = await cursor.fetchone()
            if category is None:
                raise ValueError("Категория не найдена.")
            await conn.execute(
                "INSERT INTO brands(name) VALUES (?) ON CONFLICT(name) DO NOTHING",
                (brand,),
            )
            async with conn.execute(
                "SELECT id FROM brands WHERE name = ?", (brand,)
            ) as cursor:
                brand_row = await cursor.fetchone()
            cursor = await conn.execute(
                """INSERT INTO products
                   (sku, category_id, brand_id, name, unit, package_quantity,
                    purchase_price_per_unit, current_stock, visible_to_masters,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    sku,
                    category_id,
                    brand_row["id"],
                    product_name,
                    unit,
                    decimal_text(package_quantity) if package_quantity else None,
                    decimal_text(purchase_price_per_unit)
                    if purchase_price_per_unit is not None
                    else None,
                    decimal_text(initial_stock),
                    int(visible_to_masters),
                    now,
                    now,
                ),
            )
            product_id = int(cursor.lastrowid)
            if initial_stock != ZERO:
                await conn.execute(
                    """INSERT INTO inventory_movements
                       (product_id, quantity_delta, unit, balance_before,
                        balance_after, movement_type, admin_user_id, note, created_at)
                       VALUES (?, ?, ?, '0', ?, 'INITIAL_BALANCE', ?, ?, ?)""",
                    (
                        product_id,
                        decimal_text(initial_stock),
                        unit,
                        decimal_text(initial_stock),
                        admin_user_id,
                        "Начальный остаток новой позиции",
                        now,
                    ),
                )
            if calculation_rate is not None:
                await conn.execute(
                    """INSERT INTO pricing_rules
                       (rule_key, category_id, brand_id, product_id,
                        calculation_rate, changed_by, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        f"product:{product_id}",
                        category_id,
                        brand_row["id"],
                        product_id,
                        decimal_text(calculation_rate),
                        admin_user_id,
                        now,
                    ),
                )
            return product_id

    async def set_product_field(
        self, product_id: int, field: str, value: Any
    ) -> None:
        allowed = {
            "name",
            "package_quantity",
            "purchase_price_per_unit",
            "low_stock_threshold",
            "visible_to_masters",
            "is_active",
        }
        if field not in allowed:
            raise ValueError("Это поле нельзя изменить.")
        if field in {"package_quantity", "purchase_price_per_unit", "low_stock_threshold"} and value is not None:
            amount = Decimal(str(value))
            if not amount.is_finite() or (field == "package_quantity" and amount <= ZERO) or (field != "package_quantity" and amount < ZERO):
                raise ValueError("Некорректная цена, масса упаковки или порог остатка.")
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                f"UPDATE products SET {field} = ?, updated_at = ? WHERE id = ?",
                (value, utc_now(), product_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Материал не найден.")

    async def set_product_brand(self, product_id: int, brand_name: str) -> None:
        name = brand_name.strip()
        if not name:
            raise ValueError("Название бренда не может быть пустым.")
        async with self.db.transaction() as conn:
            await conn.execute(
                "INSERT INTO brands(name) VALUES (?) ON CONFLICT(name) DO NOTHING",
                (name,),
            )
            async with conn.execute("SELECT id FROM brands WHERE name = ?", (name,)) as cursor:
                brand = await cursor.fetchone()
            cursor = await conn.execute(
                "UPDATE products SET brand_id = ?, updated_at = ? WHERE id = ?",
                (brand["id"], utc_now(), product_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Материал не найден.")
            await conn.execute(
                "UPDATE pricing_rules SET brand_id = ? WHERE product_id = ?",
                (brand["id"], product_id),
            )

    async def set_product_category(self, product_id: int, category_id: int) -> None:
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                """UPDATE products SET category_id = ?, updated_at = ?
                   WHERE id = ? AND EXISTS (
                       SELECT 1 FROM categories WHERE id = ? AND is_active = 1
                   )""",
                (category_id, utc_now(), product_id, category_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Не удалось изменить категорию материала.")
            await conn.execute(
                "UPDATE pricing_rules SET category_id = ? WHERE product_id = ?",
                (category_id, product_id),
            )

    async def search_products(
        self, query: str, *, active_only: bool = False, limit: int = 30
    ) -> list[dict[str, Any]]:
        needle = f"%{query.strip()}%"
        active = "AND p.is_active = 1" if active_only else ""
        rows = await self.db.fetchall(
            f"""SELECT p.id, p.name, p.unit, p.current_stock, p.is_active,
                       c.name AS category_name, b.name AS brand_name
                FROM products p
                JOIN categories c ON c.id = p.category_id
                JOIN brands b ON b.id = p.brand_id
                WHERE (p.name LIKE ? COLLATE NOCASE OR b.name LIKE ? COLLATE NOCASE
                       OR c.name LIKE ? COLLATE NOCASE) {active}
                ORDER BY p.is_active DESC, c.sort_order, b.name, p.name
                LIMIT ?""",
            (needle, needle, needle, limit),
        )
        return [dict(row) for row in rows]

    async def movements(
        self, *, offset: int = 0, limit: int = 20
    ) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            """SELECT m.*, p.name AS product_name, b.name AS brand_name
               FROM inventory_movements m
               JOIN products p ON p.id = m.product_id
               JOIN brands b ON b.id = p.brand_id
               ORDER BY m.id DESC LIMIT ? OFFSET ?""",
            (limit, offset),
        )
        return [dict(row) for row in rows]

    async def history(
        self,
        *,
        user_id: int | None,
        offset: int = 0,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        if user_id is None:
            rows = await self.db.fetchall(
                """SELECT * FROM calculations ORDER BY id DESC
                   LIMIT ? OFFSET ?""",
                (limit, offset),
            )
        else:
            rows = await self.db.fetchall(
                """SELECT * FROM calculations WHERE telegram_user_id = ?
                   ORDER BY id DESC LIMIT ? OFFSET ?""",
                (user_id, limit, offset),
            )
        results = []
        for row in rows:
            item_rows = await self.db.fetchall(
                """SELECT product_name_snapshot AS name, brand_snapshot AS brand,
                          quantity, unit, line_cost
                   FROM calculation_items WHERE calculation_id = ? ORDER BY id""",
                (row["id"],),
            )
            calculation = dict(row)
            calculation["items"] = [dict(item) for item in item_rows]
            results.append(calculation)
        return results

    async def get_calculation(
        self, calculation_id: int, *, owner_id: int | None
    ) -> dict[str, Any] | None:
        if owner_id is None:
            row = await self.db.fetchone(
                "SELECT * FROM calculations WHERE id = ?", (calculation_id,)
            )
        else:
            row = await self.db.fetchone(
                """SELECT * FROM calculations
                   WHERE id = ? AND telegram_user_id = ?""",
                (calculation_id, owner_id),
            )
        if row is None:
            return None
        result = dict(row)
        items = await self.db.fetchall(
            "SELECT * FROM calculation_items WHERE calculation_id = ? ORDER BY id",
            (calculation_id,),
        )
        result["items"] = [dict(item) for item in items]
        return result

    async def start_inventory_session(self, admin_user_id: int) -> int:
        now = utc_now()
        async with self.db.transaction() as conn:
            async with conn.execute(
                "SELECT id FROM inventory_sessions WHERE status = 'active' ORDER BY id DESC LIMIT 1"
            ) as cursor:
                existing = await cursor.fetchone()
            if existing is not None:
                return int(existing["id"])
            cursor = await conn.execute(
                """INSERT INTO inventory_sessions
                   (started_by, status, started_at) VALUES (?, 'active', ?)""",
                (admin_user_id, now),
            )
            session_id = int(cursor.lastrowid)
            await conn.execute(
                """INSERT INTO inventory_session_items
                   (session_id, product_id, system_balance)
                   SELECT ?, id, current_stock FROM products WHERE is_active = 1""",
                (session_id,),
            )
            return session_id

    async def active_inventory_session(self) -> int | None:
        row = await self.db.fetchone(
            "SELECT id FROM inventory_sessions WHERE status = 'active' ORDER BY id DESC LIMIT 1"
        )
        return int(row["id"]) if row else None

    async def next_session_item(self, session_id: int) -> dict[str, Any] | None:
        row = await self.db.fetchone(
            """SELECT i.product_id, i.system_balance, p.name, p.unit,
                      c.name AS category_name, b.name AS brand_name
               FROM inventory_session_items i
               JOIN products p ON p.id = i.product_id
               JOIN categories c ON c.id = p.category_id
               JOIN brands b ON b.id = p.brand_id
               WHERE i.session_id = ? AND i.status = 'pending'
               ORDER BY c.sort_order, b.name, p.name LIMIT 1""",
            (session_id,),
        )
        return _dict(row)

    async def inventory_session_action(
        self,
        *,
        session_id: int,
        product_id: int,
        admin_user_id: int,
        actual_balance: Decimal | None,
        skip: bool = False,
    ) -> dict[str, Any]:
        if actual_balance is not None and (not actual_balance.is_finite() or actual_balance < ZERO):
            raise ValueError("Фактический остаток не может быть отрицательным.")
        now = utc_now()
        async with self.db.transaction() as conn:
            async with conn.execute(
                """SELECT i.*, p.current_stock, p.unit
                   FROM inventory_session_items i
                   JOIN inventory_sessions s ON s.id = i.session_id
                   JOIN products p ON p.id = i.product_id
                   WHERE i.session_id = ? AND i.product_id = ?
                     AND i.status = 'pending' AND s.status = 'active'""",
                (session_id, product_id),
            ) as cursor:
                item = await cursor.fetchone()
            if item is None:
                return {"already_handled": True, "delta": ZERO}

            if skip:
                await conn.execute(
                    """UPDATE inventory_session_items SET status = 'skipped'
                       WHERE session_id = ? AND product_id = ?""",
                    (session_id, product_id),
                )
                await conn.execute(
                    """UPDATE inventory_sessions SET skipped_count = skipped_count + 1
                       WHERE id = ?""",
                    (session_id,),
                )
                return {"already_handled": False, "delta": ZERO}

            actual = (
                Decimal(item["current_stock"])
                if actual_balance is None
                else actual_balance
            )
            before = Decimal(item["current_stock"])
            delta = actual - before
            status = "unchanged" if delta == ZERO else "checked"
            await conn.execute(
                """UPDATE inventory_session_items
                   SET status = ?, actual_balance = ?, quantity_delta = ?
                   WHERE session_id = ? AND product_id = ?""",
                (
                    status,
                    decimal_text(actual),
                    decimal_text(delta),
                    session_id,
                    product_id,
                ),
            )
            if delta != ZERO:
                await conn.execute(
                    """UPDATE products SET current_stock = ?, updated_at = ?,
                       needs_inventory = ? WHERE id = ?""",
                    (decimal_text(actual), now, int(actual < ZERO), product_id),
                )
                await conn.execute(
                    """INSERT INTO inventory_movements
                       (product_id, quantity_delta, unit, balance_before,
                        balance_after, movement_type, inventory_session_id,
                        admin_user_id, note, created_at)
                       VALUES (?, ?, ?, ?, ?, 'INVENTORY_ADJUSTMENT', ?, ?, ?, ?)""",
                    (
                        product_id,
                        decimal_text(delta),
                        item["unit"],
                        decimal_text(before),
                        decimal_text(actual),
                        session_id,
                        admin_user_id,
                        "Корректировка полной инвентаризации",
                        now,
                    ),
                )
            totals = await self._session_totals_on(conn, session_id)
            await conn.execute(
                """UPDATE inventory_sessions
                   SET checked_count = checked_count + 1,
                       adjusted_count = adjusted_count + ?,
                       total_negative = ?,
                       total_positive = ?
                   WHERE id = ?""",
                (
                    int(delta != ZERO),
                    decimal_text(totals["negative"]),
                    decimal_text(totals["positive"]),
                    session_id,
                ),
            )
            # The handler immediately calls _send_next_session_product(),
            # which calls finish_inventory_session() when no items remain.
            # Do not mark the session completed here: that prevented the
            # completion method from returning the final summary.
            return {"already_handled": False, "delta": delta}

    @staticmethod
    async def _session_totals_on(
        conn: aiosqlite.Connection, session_id: int
    ) -> dict[str, Decimal]:
        async with conn.execute(
            """SELECT quantity_delta
                FROM inventory_session_items
                WHERE session_id = ? AND status = 'checked'
                ORDER BY id""",
            (session_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        deltas = [Decimal(row["quantity_delta"]) for row in rows]
        return {
            "negative": sum((value for value in deltas if value < ZERO), ZERO),
            "positive": sum((value for value in deltas if value > ZERO), ZERO),
        }

    async def finish_inventory_session(
        self, session_id: int, admin_user_id: int
    ) -> dict[str, Any]:
        now = utc_now()
        async with self.db.transaction() as conn:
            async with conn.execute(
                "SELECT * FROM inventory_sessions WHERE id = ? AND status = 'active'",
                (session_id,),
            ) as cursor:
                session = await cursor.fetchone()
            if session is None:
                return {"finished": False, "summary": None}
            pending = await self._fetchall_on(
                conn,
                """SELECT COUNT(*) AS count FROM inventory_session_items
                   WHERE session_id = ? AND status = 'pending'""",
                (session_id,),
            )
            remaining = int(pending[0]["count"])
            await conn.execute(
                """UPDATE inventory_session_items SET status = 'skipped'
                   WHERE session_id = ? AND status = 'pending'""",
                (session_id,),
            )
            await conn.execute(
                """UPDATE inventory_sessions SET status = 'completed',
                   completed_at = ?, skipped_count = skipped_count + ?
                   WHERE id = ?""",
                (now, remaining, session_id),
            )
            async with conn.execute(
                "SELECT * FROM inventory_sessions WHERE id = ?", (session_id,)
            ) as cursor:
                finished = await cursor.fetchone()
            return {
                "finished": True,
                "summary": dict(finished),
                "user_id": admin_user_id,
            }

    @staticmethod
    async def _fetchall_on(
        conn: aiosqlite.Connection, sql: str, parameters: tuple[Any, ...] = ()
    ) -> list[aiosqlite.Row]:
        async with conn.execute(sql, parameters) as cursor:
            return await cursor.fetchall()
