#!/usr/bin/env python3
"""End-to-end walk through the Stage 1 journeys.

This test follows the data flow in section 7 of the design document with the
live Magnificent 7 figures, so a change that breaks a journey fails here even
when every unit test still passes.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "skills" / "basket-manager" / "scripts" / "basket.py"

MAG7 = ["NVDA", "MSFT", "AAPL", "GOOGL", "AMZN", "META", "SPCX"]

FILLS = {
    "NVDA": ("6a626aa2", 0.068659, 208.1299),
    "MSFT": ("6a626aa4", 0.037476, 381.3099),
    "AAPL": ("6a626aa6", 0.044506, 321.0799),
    "GOOGL": ("6a626aa8", 0.044930, 318.0499),
    "AMZN": ("6a626aaa", 0.061141, 233.5579),
    "META": ("6a626aac", 0.023513, 607.3099),
    "SPCX": ("6a626aae", 0.122470, 116.5999),
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

    def test_create_buy_and_report(self):
        symbols = ",".join("%s:1" % s for s in MAG7)
        code, out, err = self.cli("create", "Magnificent 7 Index",
                                  "--symbols", symbols, "--account", "000000000")
        self.assertEqual(code, 0, err)
        slug = out["slug"]

        weights = dict((h["symbol"], h["target_weight_pct"]) for h in out["holdings"])
        self.assertEqual(sum(weights.values()), 100)
        self.assertEqual(weights["AAPL"], 15)
        self.assertEqual(weights["AMZN"], 15)

        code, plan, err = self.cli("plan-buy", slug, "--amount", "100")
        self.assertEqual(code, 0, err)
        self.assertEqual(round(sum(r["amount"] for r in plan["orders"]), 2), 100.0)

        orders = []
        for symbol, (order_id, shares, price) in FILLS.items():
            orders.append({
                "id": order_id, "symbol": symbol, "side": "buy", "state": "filled",
                "quantity": str(shares), "cumulative_quantity": str(shares),
                "price": str(round(price - 1.2, 4)), "average_price": str(price),
                "last_transaction_at": "2026-07-23T19:25:23.115Z",
                "executions": [{"price": str(price), "quantity": str(shares)}],
            })
        response = json.dumps({"data": {"orders": orders}})
        ids = ",".join(o["id"] for o in orders)

        code, out, err = self.cli("record-fills", slug, "--orders-json", response,
                                  "--order-ids", ids, "--account", "000000000")
        self.assertEqual(code, 0, err)
        self.assertEqual(len(out["recorded"]), 7)

        code, shown, err = self.cli("show", slug)
        self.assertEqual(code, 0, err)
        expected = sum(s * p for _, s, p in FILLS.values())
        self.assertAlmostEqual(shown["totals"]["total_invested"],
                               round(expected, 2), places=2)

        nvda = [h for h in shown["holdings"] if h["symbol"] == "NVDA"][0]
        self.assertAlmostEqual(nvda["position"]["avg_cost"], 208.1299, places=4)

    def test_a_second_record_of_the_same_orders_changes_nothing(self):
        symbols = ",".join("%s:1" % s for s in MAG7)
        slug = self.cli("create", "M7", "--symbols", symbols,
                        "--account", "000000000")[1]["slug"]
        order_id, shares, price = FILLS["NVDA"]
        response = json.dumps({"data": {"orders": [{
            "id": order_id, "symbol": "NVDA", "side": "buy", "state": "filled",
            "quantity": str(shares), "cumulative_quantity": str(shares),
            "price": "206.80", "average_price": str(price),
            "last_transaction_at": "2026-07-23T19:25:23.115Z",
            "executions": [{"price": str(price), "quantity": str(shares)}]}]}})

        self.cli("record-fills", slug, "--orders-json", response,
                 "--order-ids", order_id, "--account", "000000000")
        first = self.cli("show", slug)[1]
        self.cli("record-fills", slug, "--orders-json", response,
                 "--order-ids", order_id, "--account", "000000000")
        second = self.cli("show", slug)[1]

        del first["totals"]["built_at"], second["totals"]["built_at"]
        del first["updated_at"], second["updated_at"]
        self.assertEqual(first, second)

    def test_the_log_survives_losing_every_snapshot(self):
        slug = self.cli("create", "M7", "--symbols", "NVDA:1,MSFT:1",
                        "--account", "000000000")[1]["slug"]
        before = self.cli("show", slug)[1]
        for path in (self.data_dir / "baskets").glob("*.json"):
            path.unlink()
        after = self.cli("show", slug)[1]
        del before["totals"]["built_at"], after["totals"]["built_at"]
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
