#!/usr/bin/env python3
"""End-to-end walk through the Stage 1 journeys.

Walks one basket the whole way: `create` normalizes seven equal weights to
whole percents summing to 100, `plan-buy` splits a dollar amount across them,
`record-fills` turns real order data into the log's only source of share
counts and prices, and `show` reports the result. A change that breaks a
journey fails here even when every unit test still passes.

The figures are synthetic — same shapes and precision as a real
`get_equity_orders` response, none of the values.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "skills" / "basket-manager" / "scripts" / "basket.py"

ACCOUNT = "123456789"

# Seven synthetic symbols, matching the family used by the migration fixtures
# in tests/fixtures/. Created with equal weights, they normalize to 100 with
# the two spare percent going to the alphabetically first pair: ALFA and BETA.
BASKET_SYMBOLS = ["ALFA", "BETA", "GAMA", "DLTA", "EPSI", "ZETA", "IOTA"]

# symbol -> (order id, filled shares, average fill price)
FILLS = {
    "ALFA": ("ord-0000000001", 0.071204, 210.5501),
    "BETA": ("ord-0000000002", 0.039096, 385.1964),
    "GAMA": ("ord-0000000003", 0.046307, 325.4029),
    "DLTA": ("ord-0000000004", 0.047102, 320.1088),
    "EPSI": ("ord-0000000005", 0.063395, 236.8063),
    "ZETA": ("ord-0000000006", 0.024403, 612.7516),
    "IOTA": ("ord-0000000007", 0.127296, 118.2047),
}


class TestStageOneAcceptance(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *args):
        proc = subprocess.run(
            [sys.executable, str(CLI), "--data-dir", str(self.data_dir)] + list(args),
            capture_output=True, text=True)
        payload = json.loads(proc.stdout) if proc.stdout.strip() else None
        return proc.returncode, payload, proc.stderr

    def order_json(self, symbol):
        """Build a one-order `get_equity_orders` response for one symbol."""
        order_id, shares, price = FILLS[symbol]
        return order_id, {
            "id": order_id, "symbol": symbol, "side": "buy", "state": "filled",
            "quantity": str(shares), "cumulative_quantity": str(shares),
            # `price` is the reference price and must never reach the log.
            "price": str(round(price - 1.2, 4)), "average_price": str(price),
            "last_transaction_at": "2026-03-12T17:54:23.061Z",
            "executions": [{"price": str(price), "quantity": str(shares)}],
        }

    def test_create_buy_and_report(self):
        symbols = ",".join("%s:1" % s for s in BASKET_SYMBOLS)
        code, out, err = self.cli("create", "Core Growth Index",
                                  "--symbols", symbols, "--account", ACCOUNT)
        self.assertEqual(code, 0, err)
        slug = out["slug"]

        weights = dict((h["symbol"], h["target_weight_pct"]) for h in out["holdings"])
        self.assertEqual(sum(weights.values()), 100)
        # Seven equal weights give 14.2857 each. The two spare percent go to
        # the alphabetically first pair, so ALFA and BETA reach 15.
        self.assertEqual(weights["ALFA"], 15)
        self.assertEqual(weights["BETA"], 15)
        for symbol in ("GAMA", "DLTA", "EPSI", "ZETA", "IOTA"):
            self.assertEqual(weights[symbol], 14, symbol)

        code, plan, err = self.cli("plan-buy", slug, "--amount", "100")
        self.assertEqual(code, 0, err)
        # The allocations must sum to the requested amount to the cent.
        self.assertEqual(sum(r["amount"] for r in plan["orders"]), 100.0)

        orders = [self.order_json(s)[1] for s in FILLS]
        response = json.dumps({"data": {"orders": orders}})
        ids = ",".join(o["id"] for o in orders)

        code, out, err = self.cli("record-fills", slug, "--orders-json", response,
                                  "--order-ids", ids, "--account", ACCOUNT)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(out["recorded"]), 7)

        code, shown, err = self.cli("show", slug)
        self.assertEqual(code, 0, err)
        expected = sum(s * p for _, s, p in FILLS.values())
        self.assertAlmostEqual(shown["totals"]["total_invested"],
                               round(expected, 2), places=2)

        alfa = [h for h in shown["holdings"] if h["symbol"] == "ALFA"][0]
        self.assertAlmostEqual(alfa["position"]["avg_cost"], 210.5501, places=4)
        self.assertAlmostEqual(alfa["position"]["shares"], 0.071204, places=9)

    def test_a_second_record_of_the_same_orders_changes_nothing(self):
        symbols = ",".join("%s:1" % s for s in BASKET_SYMBOLS)
        slug = self.cli("create", "Core Growth", "--symbols", symbols,
                        "--account", ACCOUNT)[1]["slug"]
        order_id, order = self.order_json("ALFA")
        response = json.dumps({"data": {"orders": [order]}})

        self.cli("record-fills", slug, "--orders-json", response,
                 "--order-ids", order_id, "--account", ACCOUNT)
        first = self.cli("show", slug)[1]
        self.cli("record-fills", slug, "--orders-json", response,
                 "--order-ids", order_id, "--account", ACCOUNT)
        second = self.cli("show", slug)[1]

        del first["totals"]["built_at"], second["totals"]["built_at"]
        del first["updated_at"], second["updated_at"]
        self.assertEqual(first, second)

    def test_the_log_survives_losing_every_snapshot(self):
        slug = self.cli("create", "Core Growth", "--symbols", "ALFA:1,BETA:1",
                        "--account", ACCOUNT)[1]["slug"]
        before = self.cli("show", slug)[1]
        for path in (self.data_dir / "baskets").glob("*.json"):
            path.unlink()
        after = self.cli("show", slug)[1]
        del before["totals"]["built_at"], after["totals"]["built_at"]
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
