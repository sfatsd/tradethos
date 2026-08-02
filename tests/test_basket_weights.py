#!/usr/bin/env python3
"""Unit tests for basket_weights.py."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "basket-manager" / "scripts"))

from basket_weights import MAX_HOLDINGS, WeightError, normalize_weights, refill


class TestNormalizeWeights(unittest.TestCase):

    def test_seven_equal_holdings(self):
        # 100/7 = 14.2857. Seven floors of 14 sum to 98, so two holdings gain
        # one percent. All fractional parts tie, so the alphabetical order
        # decides: AAPL and AMZN.
        symbols = ["NVDA", "MSFT", "AAPL", "GOOGL", "AMZN", "META", "SPCX"]
        result = normalize_weights({s: 1 for s in symbols})
        self.assertEqual(sum(result.values()), 100)
        self.assertEqual(result["AAPL"], 15)
        self.assertEqual(result["AMZN"], 15)
        for symbol in ["GOOGL", "META", "MSFT", "NVDA", "SPCX"]:
            self.assertEqual(result[symbol], 14)

    def test_ratio_input(self):
        result = normalize_weights({"NVDA": 2, "MSFT": 1})
        self.assertEqual(result, {"NVDA": 67, "MSFT": 33})

    def test_ratio_above_one_hundred(self):
        # An input carries no upper bound, because it can be a ratio.
        result = normalize_weights({"NVDA": 200, "MSFT": 100})
        self.assertEqual(result, {"NVDA": 67, "MSFT": 33})

    def test_three_equal_holdings(self):
        result = normalize_weights({"A": 1, "B": 1, "C": 1})
        self.assertEqual(sum(result.values()), 100)
        self.assertEqual(result, {"A": 34, "B": 33, "C": 33})

    def test_already_normal_set_is_unchanged(self):
        weights = {"NVDA": 50, "MSFT": 30, "AAPL": 20}
        self.assertEqual(normalize_weights(weights), weights)

    def test_normalization_is_repeatable(self):
        once = normalize_weights({s: 1 for s in ["AAPL", "AMZN", "GOOGL"]})
        twice = normalize_weights(once)
        self.assertEqual(once, twice)

    def test_decimal_input_is_allowed(self):
        result = normalize_weights({"NVDA": 14.29, "MSFT": 14.29})
        self.assertEqual(result, {"NVDA": 50, "MSFT": 50})

    def test_empty_set_gives_empty_result(self):
        self.assertEqual(normalize_weights({}), {})

    def test_zero_weight_is_refused(self):
        with self.assertRaises(WeightError) as ctx:
            normalize_weights({"NVDA": 0, "MSFT": 1})
        self.assertIn("NVDA", str(ctx.exception))

    def test_negative_weight_is_refused(self):
        with self.assertRaises(WeightError):
            normalize_weights({"NVDA": -5, "MSFT": 1})

    def test_non_numeric_weight_is_refused(self):
        with self.assertRaises(WeightError):
            normalize_weights({"NVDA": "many", "MSFT": 1})

    def test_boolean_weight_is_refused(self):
        # bool is a subclass of int; it must not pass as a weight.
        with self.assertRaises(WeightError):
            normalize_weights({"NVDA": True, "MSFT": 1})

    def test_too_many_holdings_is_refused(self):
        with self.assertRaises(WeightError) as ctx:
            normalize_weights({"S%d" % i: 1 for i in range(MAX_HOLDINGS + 1)})
        self.assertIn("100", str(ctx.exception))

    def test_result_that_would_round_to_zero_is_refused(self):
        # 1000:1 scales the second holding below half a percent.
        with self.assertRaises(WeightError) as ctx:
            normalize_weights({"NVDA": 1000, "MSFT": 1})
        self.assertIn("MSFT", str(ctx.exception))


class TestRefill(unittest.TestCase):

    def test_proportional_keeps_ratios(self):
        # NVDA drops to 20, leaving 80 for MSFT 30 and AAPL 20.
        result = refill({"MSFT": 30, "AAPL": 20}, 80, "proportional")
        self.assertEqual(result, {"MSFT": 48, "AAPL": 32})

    def test_equal_flattens(self):
        result = refill({"MSFT": 30, "AAPL": 20}, 80, "equal")
        self.assertEqual(result, {"MSFT": 40, "AAPL": 40})

    def test_proportional_is_the_default(self):
        self.assertEqual(
            refill({"MSFT": 30, "AAPL": 20}, 80),
            refill({"MSFT": 30, "AAPL": 20}, 80, "proportional"),
        )

    def test_result_sums_to_room(self):
        result = refill({"A": 1, "B": 1, "C": 1}, 70)
        self.assertEqual(sum(result.values()), 70)

    def test_room_below_holding_count_is_refused(self):
        # Two holdings cannot share one percent without one falling to 0.
        with self.assertRaises(WeightError):
            refill({"MSFT": 30, "AAPL": 20}, 1)

    def test_a_skewed_set_that_would_round_to_zero_is_refused(self):
        # room >= len(others) is not enough on its own. 49:1 over 2 percent
        # gives 1.96 and 0.04, and the shortfall goes to the larger holding.
        with self.assertRaises(WeightError) as ctx:
            refill({"MSFT": 49, "AAPL": 1}, 2)
        self.assertIn("AAPL", str(ctx.exception))

    def test_unknown_mode_is_refused(self):
        with self.assertRaises(WeightError):
            refill({"MSFT": 30}, 80, "sideways")

    def test_empty_others_gives_empty_result(self):
        self.assertEqual(refill({}, 0), {})


if __name__ == "__main__":
    unittest.main()
