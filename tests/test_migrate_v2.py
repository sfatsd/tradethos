#!/usr/bin/env python3
"""Tests for the Stage 2 migration."""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "basket-manager" / "scripts"))

from migrate_v2 import (
    SHARE_TOLERANCE,
    attribute_orders,
    normalize_basket_weights,
    read_baskets,
)

FIXTURES = ROOT / "tests" / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


class TestReadBaskets(unittest.TestCase):

    def setUp(self):
        self.baskets = read_baskets(load("live_watchlists.json"))
        self.by_slug = dict((b["slug"], b) for b in self.baskets)

    def test_finds_every_basket_and_skips_ordinary_lists(self):
        self.assertEqual(len(self.baskets), 6)
        self.assertNotIn("my-first-list", self.by_slug)

    def test_reads_weights_and_snapshots(self):
        mag7 = self.by_slug["magnificent-7-index"]
        self.assertEqual(len(mag7["weights"]), 7)
        self.assertEqual(len(mag7["snapshot"]), 7)

    def test_a_basket_without_a_snapshot_reads_as_definition_only(self):
        semi = self.by_slug["semiconductor-etf-style"]
        self.assertEqual(len(semi["weights"]), 10)
        self.assertEqual(semi["snapshot"], {})

    def test_a_basket_with_no_watchlist_items_still_reads(self):
        # test-250 and ai-20 have item_count 0 but carry full metadata.
        self.assertIn("test-250", self.by_slug)
        self.assertEqual(len(self.by_slug["test-250"]["weights"]), 12)


class TestNormalizeBasketWeights(unittest.TestCase):

    def test_a_weight_set_that_sums_to_120_is_normalized(self):
        weights = dict(("S%d" % i, 10) for i in range(12))
        normalized, changed = normalize_basket_weights(weights)
        self.assertEqual(sum(normalized.values()), 100)
        self.assertTrue(changed)

    def test_an_already_whole_set_reports_no_change(self):
        normalized, changed = normalize_basket_weights({"A": 60, "B": 40})
        self.assertEqual(normalized, {"A": 60, "B": 40})
        self.assertFalse(changed)

    def test_fractional_weights_become_whole_numbers(self):
        normalized, _ = normalize_basket_weights({"A": 14.29, "B": 14.29, "C": 71.42})
        self.assertEqual(sum(normalized.values()), 100)
        for value in normalized.values():
            self.assertEqual(value, int(value))


class TestAttribution(unittest.TestCase):

    def setUp(self):
        self.baskets = read_baskets(load("live_watchlists.json"))
        self.orders = load("live_orders.json")["data"]["orders"]
        self.assignments, self.unattributed = attribute_orders(self.baskets, self.orders)

    def slug_for(self, symbol, shares):
        for order in self.orders:
            if order["symbol"] == symbol and abs(float(order["cumulative_quantity"]) - shares) < 1e-9:
                return self.assignments.get(order["id"])
        self.fail("no order for %s %s" % (symbol, shares))

    def test_lite_orders_split_across_two_baskets(self):
        # 0.029940 matches Optical's 0.02996; 0.011975 matches Storage's 0.01198.
        self.assertEqual(self.slug_for("LITE", 0.029940), "optical-and-photonics-in")
        self.assertEqual(self.slug_for("LITE", 0.011975), "storage-and-memory-index")

    def test_mu_goes_to_storage_because_semiconductor_claims_nothing(self):
        self.assertEqual(self.slug_for("MU", 0.020247), "storage-and-memory-index")

    def test_the_first_nvda_order_goes_to_magnificent_seven(self):
        self.assertEqual(self.slug_for("NVDA", 0.068659), "magnificent-7-index")

    def test_the_standalone_nvda_order_is_unattributed(self):
        # Bought 27 July outside any basket. Leaving it out is correct.
        self.assertIsNone(self.slug_for("NVDA", 0.048067))
        ids = [o["id"] for o in self.unattributed]
        self.assertIn("REDACTEDC5-0000-0000-0000-000000000023", ids)

    def test_exactly_one_order_is_unattributed(self):
        self.assertEqual(len(self.unattributed), 1)

    def test_every_other_order_is_assigned(self):
        assigned = [o for o in self.orders if self.assignments.get(o["id"])]
        self.assertEqual(len(assigned), 19)

    def test_no_order_is_assigned_to_two_baskets(self):
        self.assertEqual(len(set(self.assignments)), len(self.assignments))

    def test_a_basket_without_a_snapshot_receives_no_orders(self):
        owned = [s for s in self.assignments.values() if s == "semiconductor-etf-style"]
        self.assertEqual(owned, [])

    def test_tolerance_absorbs_the_observed_drift(self):
        # IPGP is the worst real case: claimed 0.05193, filled 0.051824.
        self.assertGreater(SHARE_TOLERANCE, 0.000106)
        self.assertEqual(self.slug_for("IPGP", 0.051824), "optical-and-photonics-in")

    def test_tolerance_is_tight_enough_to_separate_competing_claims(self):
        # LITE's two claims differ by 0.018; the tolerance must be far below that.
        self.assertLess(SHARE_TOLERANCE, 0.018 / 2)


if __name__ == "__main__":
    unittest.main()
