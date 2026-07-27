#!/usr/bin/env python3
"""Unit tests for basket_utils.py module."""

import sys
import unittest
from pathlib import Path

# Add project root and skills/basket-manager/scripts to sys.path
root_dir = Path(__file__).resolve().parents[1]
scripts_dir = root_dir / "skills" / "basket-manager" / "scripts"
sys.path.insert(0, str(scripts_dir))
sys.path.insert(0, str(root_dir))

from basket_utils import (
    decode_watchlist_metadata,
    encode_watchlist_metadata,
    update_basket_watchlist_baseline,
    watchlist_to_basket_dict,
)


class TestBasketUtils(unittest.TestCase):
    def test_encode_decode_watchlist_metadata(self):
        slug = "storage-leaders"
        target_weights = {"WDC": 30.0, "STX": 30.0, "MU": 40.0}
        threshold = 5.0
        snapshot = {"ts": "2026-07-24T00:00:00Z", "h": {}}

        encoded = encode_watchlist_metadata(slug, target_weights, threshold, snapshot=snapshot)
        self.assertTrue(encoded.startswith("Z64:"))

        decoded = decode_watchlist_metadata(encoded)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded["slug"], slug)
        self.assertEqual(decoded["weights"], target_weights)
        self.assertEqual(decoded["threshold"], threshold)

    def test_large_basket_compression(self):
        symbols = [f"SYM{i}" for i in range(30)]
        target_weights = {sym: 3.33 for sym in symbols}
        snap_h = {sym: [10.5, 100.25] for sym in symbols}
        snapshot = {"ts": 1721861640, "h": snap_h}

        encoded = encode_watchlist_metadata("large-30-symbol-basket", target_weights, 5.0, snapshot=snapshot)
        self.assertTrue(encoded.startswith("Z64:"))
        self.assertLessEqual(len(encoded), 256)

        decoded = decode_watchlist_metadata(encoded)
        self.assertIsNotNone(decoded)
        self.assertEqual(len(decoded["weights"]), 30)

    def test_decode_invalid_metadata(self):
        self.assertIsNone(decode_watchlist_metadata(None))
        self.assertIsNone(decode_watchlist_metadata("Just a normal watchlist description"))
        self.assertIsNone(decode_watchlist_metadata("[BASKET_MODEL] invalid json"))


    def test_watchlist_to_basket_dict(self):
        desc = encode_watchlist_metadata("storage-leaders", {"WDC": 50.0, "STX": 50.0}, rebalance_threshold_pct=5.0)
        reconstructed = {
            "WDC": {"shares": 10.0, "avg_cost": 50.0, "total_cost": 500.0},
        }

        b_dict = watchlist_to_basket_dict("Basket: Storage Leaders", desc, reconstructed)

        self.assertEqual(b_dict["name"], "Storage Leaders")
        self.assertEqual(b_dict["slug"], "storage-leaders")
        self.assertEqual(b_dict["total_invested"], 500.0)
        self.assertEqual(len(b_dict["holdings"]), 2)

        wdc_h = next(h for h in b_dict["holdings"] if h["symbol"] == "WDC")
        self.assertIsNotNone(wdc_h["position"])
        self.assertEqual(wdc_h["position"]["shares"], 10.0)

        stx_h = next(h for h in b_dict["holdings"] if h["symbol"] == "STX")
        self.assertIsNone(stx_h["position"])

    def test_update_basket_watchlist_baseline(self):
        desc = encode_watchlist_metadata("storage-leaders", {"WDC": 50.0, "STX": 50.0})

        # Buy 10 WDC @ $50
        desc_updated = update_basket_watchlist_baseline(desc, "WDC", "buy", 10.0, 50.0, timestamp=1721861640)
        decoded = decode_watchlist_metadata(desc_updated)

        self.assertIsNotNone(decoded["snapshot"])
        snap_h = decoded["snapshot"]["h"]
        self.assertIn("WDC", snap_h)
        self.assertEqual(snap_h["WDC"], [10.0, 50.0])

        # Buy another 10 WDC @ $60
        desc_updated_2 = update_basket_watchlist_baseline(desc_updated, "WDC", "buy", 10.0, 60.0, timestamp=1721862000)
        decoded_2 = decode_watchlist_metadata(desc_updated_2)

        snap_h_2 = decoded_2["snapshot"]["h"]
        # 20 shares @ $55 avg cost
        self.assertEqual(snap_h_2["WDC"], [20.0, 55.0])


if __name__ == "__main__":
    unittest.main()
