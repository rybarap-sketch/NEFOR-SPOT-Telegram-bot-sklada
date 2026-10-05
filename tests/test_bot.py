from __future__ import annotations

import sqlite3
import tempfile
import unittest
from decimal import Decimal
from os import environ
from pathlib import Path
from unittest.mock import patch

from salon_cost_bot.config import DEFAULT_ADMIN_USER_ID, Settings
from salon_cost_bot.database import Database
from salon_cost_bot.domain import parse_decimal
from salon_cost_bot.handlers.common import is_admin
from salon_cost_bot.services import CalculationLine, SalonService


class BotDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.database_path = self.root / "data" / "test.sqlite3"
        self.db = Database(self.database_path)
        await self.db.open()
        self.service = SalonService(self.db)

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.temp_dir.cleanup()

    async def product_id(self, name: str, brand: str) -> int:
        row = await self.db.fetchone(
            """SELECT p.id FROM products p
               JOIN brands b ON b.id = p.brand_id
               WHERE p.name = ? AND b.name = ?""",
            (name, brand),
        )
        self.assertIsNotNone(row)
        return int(row["id"])

    async def test_seed_is_repeatable_and_does_not_reset_stock(self) -> None:
        count_before = await self.db.fetchone("SELECT COUNT(*) AS count FROM products")
        product_id = await self.product_id("Порошок", "Secta")
        await self.service.adjust_inventory(
            product_id=product_id,
            actual_balance=Decimal("317.5"),
            admin_user_id=DEFAULT_ADMIN_USER_ID,
        )
        await self.db.seed_initial_data()
        count_after = await self.db.fetchone("SELECT COUNT(*) AS count FROM products")
        product = await self.service.get_product(product_id)

        self.assertEqual(count_before["count"], count_after["count"])
        self.assertEqual(Decimal(product["current_stock"]), Decimal("317.5"))

    async def test_decimal_calculation_snapshots_and_idempotency(self) -> None:
        self.assertEqual(parse_decimal("12,5"), Decimal("12.5"))
        paint_id = await self.product_id("9.18", "EPICA")
        pigment_id = await self.product_id("Absinthe (Зелёный)", "Bad Girl")
        lines = [
            CalculationLine(paint_id, Decimal("37")),
            CalculationLine(pigment_id, Decimal("10")),
        ]

        calculation_id, total, saved, created = await self.service.complete_calculation(
            completion_token="test-calc-1",
            telegram_user_id=101,
            username="master",
            full_name="Тестовый мастер",
            lines=lines,
        )
        self.assertTrue(created)
        self.assertEqual(total, Decimal("394.40"))
        self.assertEqual(
            [line["cost"] for line in saved],
            [Decimal("329.30"), Decimal("65.10")],
        )

        paint = await self.service.get_product(paint_id)
        pigment = await self.service.get_product(pigment_id)
        self.assertEqual(Decimal(paint["current_stock"]), Decimal("-37"))
        self.assertEqual(Decimal(pigment["current_stock"]), Decimal("288"))

        repeated_id, repeated_total, _, repeated_created = (
            await self.service.complete_calculation(
                completion_token="test-calc-1",
                telegram_user_id=101,
                username="master",
                full_name="Тестовый мастер",
                lines=lines,
            )
        )
        self.assertEqual(repeated_id, calculation_id)
        self.assertEqual(repeated_total, total)
        self.assertFalse(repeated_created)
        movement_count = await self.db.fetchone(
            "SELECT COUNT(*) AS count FROM inventory_movements WHERE calculation_id = ?",
            (calculation_id,),
        )
        self.assertEqual(movement_count["count"], 2)

        previous, new_rate = await self.service.change_price(
            paint_id, Decimal("10.00"), DEFAULT_ADMIN_USER_ID
        )
        self.assertEqual(previous, Decimal("8.90"))
        self.assertEqual(new_rate, Decimal("10.00"))
        old_item = await self.db.fetchone(
            """SELECT calculation_rate_snapshot FROM calculation_items
               WHERE calculation_id = ? AND product_id = ?""",
            (calculation_id, paint_id),
        )
        self.assertEqual(
            Decimal(old_item["calculation_rate_snapshot"]), Decimal("8.90")
        )

        _, updated_total, _, _ = await self.service.complete_calculation(
            completion_token="test-calc-2",
            telegram_user_id=101,
            username="master",
            full_name="Тестовый мастер",
            lines=[CalculationLine(paint_id, Decimal("10"))],
        )
        self.assertEqual(updated_total, Decimal("100.00"))

    async def test_failed_calculation_rolls_back_stock_and_history(self) -> None:
        product_id = await self.product_id("9.18", "EPICA")
        await self.db.conn.execute(
            """CREATE TRIGGER reject_calculation_movement
               BEFORE INSERT ON inventory_movements
               WHEN NEW.movement_type = 'CALCULATION_USAGE'
               BEGIN SELECT RAISE(ABORT, 'simulated ledger failure'); END"""
        )
        with self.assertRaises(sqlite3.IntegrityError):
            await self.service.complete_calculation(
                completion_token="test-calc-rollback",
                telegram_user_id=101,
                username=None,
                full_name="Тестовый мастер",
                lines=[CalculationLine(product_id, Decimal("12"))],
            )
        product = await self.service.get_product(product_id)
        calculation_count = await self.db.fetchone(
            "SELECT COUNT(*) AS count FROM calculations"
        )
        self.assertEqual(Decimal(product["current_stock"]), Decimal("0"))
        self.assertEqual(calculation_count["count"], 0)

    async def test_receipt_writeoff_count_and_low_stock_attention(self) -> None:
        category = await self.db.fetchone(
            "SELECT id FROM categories WHERE category_key = 'small_consumable'"
        )
        product_id = await self.service.create_product(
            category_id=int(category["id"]),
            brand_name="Тестовый бренд",
            name="Тестовая фольга",
            unit="g",
            package_quantity=None,
            purchase_price_per_unit=None,
            calculation_rate=None,
            initial_stock=Decimal("5"),
            visible_to_masters=False,
            admin_user_id=DEFAULT_ADMIN_USER_ID,
        )
        before, after = await self.service.receipt(
            product_id=product_id,
            quantity=Decimal("200"),
            package_quantity=Decimal("100"),
            package_count=Decimal("2"),
            price_per_package=Decimal("300"),
            admin_user_id=DEFAULT_ADMIN_USER_ID,
        )
        self.assertEqual((before, after), (Decimal("5"), Decimal("205")))
        await self.service.write_off(
            product_id=product_id,
            quantity=Decimal("7"),
            reason="Повреждение упаковки",
            admin_user_id=DEFAULT_ADMIN_USER_ID,
        )
        before, actual, delta = await self.service.adjust_inventory(
            product_id=product_id,
            actual_balance=Decimal("190"),
            admin_user_id=DEFAULT_ADMIN_USER_ID,
        )
        self.assertEqual((before, actual, delta), (Decimal("198"), Decimal("190"), Decimal("-8")))
        await self.service.set_product_field(product_id, "low_stock_threshold", "200")

        attention = await self.service.inventory(
            attention_only=True, limit=200
        )
        self.assertIn(product_id, {int(item["id"]) for item in attention})
        product = await self.service.get_product(product_id)
        self.assertEqual(Decimal(product["current_stock"]), Decimal("190"))
        self.assertEqual(product["purchase_price_per_unit"], "3")

        movements = await self.db.fetchall(
            "SELECT movement_type FROM inventory_movements WHERE product_id = ? ORDER BY id",
            (product_id,),
        )
        self.assertEqual(
            [row["movement_type"] for row in movements],
            ["INITIAL_BALANCE", "RECEIPT", "WRITE_OFF", "INVENTORY_ADJUSTMENT"],
        )

    async def test_full_inventory_session_records_adjustments_and_skips(self) -> None:
        session_id = await self.service.start_inventory_session(DEFAULT_ADMIN_USER_ID)
        item = await self.service.next_session_item(session_id)
        self.assertIsNotNone(item)
        actual = Decimal(item["system_balance"]) + Decimal("0.5")

        result = await self.service.inventory_session_action(
            session_id=session_id,
            product_id=int(item["product_id"]),
            admin_user_id=DEFAULT_ADMIN_USER_ID,
            actual_balance=actual,
        )
        self.assertEqual(result["delta"], Decimal("0.5"))
        self.assertTrue(
            (
                await self.service.inventory_session_action(
                    session_id=session_id,
                    product_id=int(item["product_id"]),
                    admin_user_id=DEFAULT_ADMIN_USER_ID,
                    actual_balance=actual,
                )
            )["already_handled"]
        )
        finished = await self.service.finish_inventory_session(
            session_id, DEFAULT_ADMIN_USER_ID
        )
        summary = finished["summary"]
        self.assertEqual(summary["checked_count"], 1)
        self.assertEqual(summary["adjusted_count"], 1)
        self.assertGreater(summary["skipped_count"], 0)
        self.assertEqual(Decimal(summary["total_positive"]), Decimal("0.5"))
        self.assertIsNotNone(summary["completed_at"])

    async def test_forward_schema_upgrade_keeps_data_and_creates_backup(self) -> None:
        product_id = await self.product_id("9.18", "EPICA")
        calculation_id, _, _, _ = await self.service.complete_calculation(
            completion_token="test-calc-migration",
            telegram_user_id=101,
            username=None,
            full_name="Тестовый мастер",
            lines=[CalculationLine(product_id, Decimal("5"))],
        )
        await self.db.close()
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("PRAGMA user_version = 0")

        self.db = Database(self.database_path)
        await self.db.open()
        self.service = SalonService(self.db)
        calculation = await self.service.get_calculation(
            calculation_id, owner_id=101
        )
        backups = list((self.database_path.parent / "backups").glob("pre-migration-*.sqlite3"))
        self.assertEqual(calculation["completion_token"], "test-calc-migration")
        self.assertTrue(backups)
        backup_size = backups[0].stat().st_size
        self.assertGreater(backup_size, 0)

    async def test_admin_access_uses_only_configured_telegram_id(self) -> None:
        settings = Settings(
            bot_token="unused-test-token",
            admin_user_id=DEFAULT_ADMIN_USER_ID,
            database_path=self.database_path,
            app_env="test",
            log_level="INFO",
        )
        self.assertTrue(is_admin(settings, DEFAULT_ADMIN_USER_ID))
        self.assertFalse(is_admin(settings, DEFAULT_ADMIN_USER_ID + 1))
        self.assertFalse(is_admin(settings, None))

    async def test_explicit_database_path_overrides_shared_database_url(self) -> None:
        with patch.dict(
            environ,
            {
                "BOT_TOKEN": "unit-test-placeholder",
                "DATABASE_PATH": str(self.root / "bot.sqlite3"),
                "DATABASE_URL": "postgresql://unrelated-service",
            },
        ):
            settings = Settings.from_env()
        self.assertEqual(settings.database_path, self.root / "bot.sqlite3")


if __name__ == "__main__":
    unittest.main()
