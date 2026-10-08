import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from salon_cost_bot.payouts import calculate_payout


class PayoutTests(unittest.TestCase):
    def test_expected_split(self):
        p = calculate_payout(Decimal("8000"), Decimal("878.70"))
        self.assertEqual(p.master_share, Decimal("3916.72"))
        self.assertEqual(p.salon_share, Decimal("3204.58"))
        self.assertEqual(p.payable_to_salon, Decimal("4083.28"))

    def test_reconciles_kopecks(self):
        for price, cost in [("100.01", "1.99"), ("50", "0"), ("1.01", "1")]:
            p = calculate_payout(Decimal(price), Decimal(cost))
            self.assertEqual(p.master_share + p.payable_to_salon, p.service_price)
            self.assertEqual(p.master_share + p.salon_share, p.distributable)

    def test_equal_cost(self):
        p = calculate_payout(Decimal("200"), Decimal("200"))
        self.assertEqual(p.master_share, Decimal("0"))
        self.assertEqual(p.payable_to_salon, Decimal("200"))

    def test_below_materials_rejected(self):
        with self.assertRaises(ValueError):
            calculate_payout(Decimal("199"), Decimal("200"))

    def test_bad_price_rejected(self):
        for n in ("0", "-1", "NaN", "Infinity"):
            with self.assertRaises(ValueError):
                calculate_payout(Decimal(n), Decimal("10"))


if __name__ == "__main__":
    unittest.main()
