import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Add project root and scripts directory to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

import list_symbols
import basket_summary
import calc_performance
import calc_drift


class TestListSymbols(unittest.TestCase):

    def setUp(self):
        self.sample_basket = {
            "name": "Test Basket",
            "description": "Testing basket",
            "created_at": "2026-07-24T00:00:00Z",
            "updated_at": "2026-07-24T00:00:00Z",
            "holdings": [
                {"symbol": "AAPL", "target_weight_pct": 50.0},
                {"symbol": "MSFT", "target_weight_pct": 50.0},
            ]
        }

    def test_load_basket(self):
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".json", delete=False) as tf:
            json.dump(self.sample_basket, tf)
            tf_path = Path(tf.name)

        try:
            loaded = list_symbols.load_basket(tf_path)
            self.assertEqual(loaded["name"], "Test Basket")
            self.assertEqual(len(loaded["holdings"]), 2)
        finally:
            os.remove(tf_path)


class TestBasketSummary(unittest.TestCase):

    def test_summarize_basket_positioned(self):
        basket_data = {
            "name": "Tech Index",
            "description": "Tech stocks",
            "total_invested": 100.0,
            "holdings": [
                {
                    "symbol": "AAPL",
                    "target_weight_pct": 50.0,
                    "position": {"shares": 0.5, "avg_cost": 200.0, "total_invested": 100.0, "transactions": []}
                },
                {
                    "symbol": "GOOGL",
                    "target_weight_pct": 50.0,
                    "position": None
                }
            ]
        }
        summary = basket_summary.summarize_basket(basket_data)
        self.assertEqual(summary["name"], "Tech Index")
        self.assertEqual(summary["holdings_count"], 2)
        self.assertEqual(summary["positioned_count"], 1)
        self.assertEqual(summary["total_weight_pct"], 100.0)
        self.assertEqual(summary["weight_status"], "ok")
        self.assertEqual(summary["total_invested"], 100.0)

    def test_summarize_basket_unbalanced_weights(self):
        basket_data = {
            "name": "Overweighted",
            "holdings": [
                {"symbol": "A", "target_weight_pct": 60.0},
                {"symbol": "B", "target_weight_pct": 60.0},
            ]
        }
        summary = basket_summary.summarize_basket(basket_data)
        self.assertEqual(summary["weight_status"], "over")


class TestCalcPerformance(unittest.TestCase):

    def test_calc_holding_perf_with_position(self):
        holding = {
            "symbol": "NVDA",
            "target_weight_pct": 100.0,
            "position": {
                "shares": 10.0,
                "avg_cost": 150.0,
                "total_invested": 1500.0,
                "transactions": []
            }
        }
        prices = {"NVDA": 200.0}
        perf = calc_performance.calc_holding_perf(holding, prices)
        self.assertEqual(perf["symbol"], "NVDA")
        self.assertEqual(perf["current_value"], 2000.0)
        self.assertEqual(perf["pnl"], 500.0)
        self.assertEqual(perf["pnl_pct"], 33.33)

    def test_calc_holding_perf_without_position(self):
        holding = {
            "symbol": "AMD",
            "target_weight_pct": 50.0,
            "position": None
        }
        prices = {"AMD": 100.0}
        perf = calc_performance.calc_holding_perf(holding, prices)
        self.assertFalse(perf["has_position"])
        self.assertEqual(perf["current_value"], 0)
        self.assertEqual(perf["pnl"], 0)

    def test_calc_basket_perf(self):
        basket = {
            "name": "Mini Basket",
            "holdings": [
                {
                    "symbol": "AAPL",
                    "target_weight_pct": 50.0,
                    "position": {"shares": 2.0, "avg_cost": 100.0, "total_invested": 200.0, "transactions": []}
                },
                {
                    "symbol": "MSFT",
                    "target_weight_pct": 50.0,
                    "position": {"shares": 1.0, "avg_cost": 300.0, "total_invested": 300.0, "transactions": []}
                }
            ]
        }
        prices = {"AAPL": 150.0, "MSFT": 350.0}
        perf = calc_performance.calc_basket_perf(basket, prices)
        self.assertEqual(perf["basket"], "Mini Basket")
        self.assertEqual(perf["total_invested"], 500.0)
        self.assertEqual(perf["current_value"], 650.0)  # (2*150) + (1*350) = 300 + 350 = 650
        self.assertEqual(perf["total_pnl"], 150.0)
        self.assertEqual(perf["total_pnl_pct"], 30.0)


class TestCalcDrift(unittest.TestCase):

    def test_classify_drift(self):
        self.assertEqual(calc_drift.classify_drift(1.0, 2.0, 5.0), "on_target")
        self.assertEqual(calc_drift.classify_drift(3.5, 2.0, 5.0), "minor_drift")
        self.assertEqual(calc_drift.classify_drift(6.0, 2.0, 5.0), "significant_drift")

    def test_calc_basket_drift(self):
        basket = {
            "name": "Drift Test Basket",
            "rebalance_threshold_pct": 5.0,
            "holdings": [
                {
                    "symbol": "WDC",
                    "target_weight_pct": 50.0,
                    "position": {"shares": 10.0, "avg_cost": 100.0, "total_invested": 1000.0, "transactions": []}
                },
                {
                    "symbol": "STX",
                    "target_weight_pct": 50.0,
                    "position": {"shares": 10.0, "avg_cost": 100.0, "total_invested": 1000.0, "transactions": []}
                }
            ]
        }
        # WDC doubles in price (now value = 2000), STX stays at 100 (value = 1000). Total value = 3000
        # WDC actual weight = 2000/3000 = 66.67% (drift +16.67% -> significant drift)
        # STX actual weight = 1000/3000 = 33.33% (drift -16.67% -> significant drift)
        prices = {"WDC": 200.0, "STX": 100.0}
        drift_res = calc_drift.calc_basket_drift(basket, prices, threshold=5.0, on_target=2.0)

        self.assertTrue(drift_res["rebalance_needed"])
        self.assertEqual(len(drift_res["flagged"]), 2)
        self.assertIn("WDC", drift_res["flagged"])
        self.assertIn("STX", drift_res["flagged"])


if __name__ == "__main__":
    unittest.main()
