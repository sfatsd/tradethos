"""Unit tests for the pure calculation functions in calc_performance.py and
calc_drift.py.

These functions take a plain basket dict (the shape basket_store.snapshot_dict
produces) and a --prices dict. They do not touch the event log, so these
tests construct the input dicts directly rather than replaying a store.
"""

import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "skills" / "basket-manager" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import calc_performance
import calc_drift


class TestCalcPerformance(unittest.TestCase):

    def test_calc_holding_perf_with_position(self):
        holding = {
            "symbol": "NVDA",
            "target_weight_pct": 100,
            "position": {"shares": 10.0, "avg_cost": 150.0, "total_invested": 1500.0,
                        "realized_pnl": 0.0},
        }
        prices = {"NVDA": 200.0}
        perf = calc_performance.calc_holding_perf(holding, prices)
        self.assertEqual(perf["symbol"], "NVDA")
        self.assertEqual(perf["current_value"], 2000.0)
        self.assertEqual(perf["pnl"], 500.0)
        self.assertEqual(perf["pnl_pct"], 33.33)

    def test_calc_holding_perf_without_position(self):
        holding = {"symbol": "AMD", "target_weight_pct": 50, "position": None}
        prices = {"AMD": 100.0}
        perf = calc_performance.calc_holding_perf(holding, prices)
        self.assertFalse(perf["has_position"])
        self.assertEqual(perf["current_value"], 0)
        self.assertEqual(perf["pnl"], 0)

    def test_calc_basket_perf(self):
        basket = {
            "name": "Mini Basket",
            "holdings": [
                {"symbol": "AAPL", "target_weight_pct": 50,
                 "position": {"shares": 2.0, "avg_cost": 100.0, "total_invested": 200.0,
                              "realized_pnl": 0.0}},
                {"symbol": "MSFT", "target_weight_pct": 50,
                 "position": {"shares": 1.0, "avg_cost": 300.0, "total_invested": 300.0,
                              "realized_pnl": 0.0}},
            ],
        }
        prices = {"AAPL": 150.0, "MSFT": 350.0}
        perf = calc_performance.calc_basket_perf(basket, prices)
        self.assertEqual(perf["basket"], "Mini Basket")
        self.assertEqual(perf["total_invested"], 500.0)
        self.assertEqual(perf["current_value"], 650.0)  # (2*150) + (1*350)
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
                {"symbol": "WDC", "target_weight_pct": 50,
                 "position": {"shares": 10.0, "avg_cost": 100.0, "total_invested": 1000.0,
                              "realized_pnl": 0.0}},
                {"symbol": "STX", "target_weight_pct": 50,
                 "position": {"shares": 10.0, "avg_cost": 100.0, "total_invested": 1000.0,
                              "realized_pnl": 0.0}},
            ],
        }
        # WDC doubles to 200: value 2000. STX stays at 100: value 1000. Total 3000.
        # WDC actual weight 66.67% -> drift +16.67% (significant).
        # STX actual weight 33.33% -> drift -16.67% (significant).
        prices = {"WDC": 200.0, "STX": 100.0}
        drift_res = calc_drift.calc_basket_drift(basket, prices, threshold=5.0, on_target=2.0)

        self.assertTrue(drift_res["rebalance_needed"])
        self.assertEqual(len(drift_res["flagged"]), 2)
        self.assertIn("WDC", drift_res["flagged"])
        self.assertIn("STX", drift_res["flagged"])


if __name__ == "__main__":
    unittest.main()
