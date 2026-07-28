#!/usr/bin/env python3
"""Unit tests for basket_utils.py module."""

import datetime
import json
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
    iter_watchlist_baskets,
    parse_watchlists_json,
    reconstruct_basket_positions,
    update_basket_watchlist_baseline,
    watchlist_to_basket_dict,
)

# Verbatim shapes captured from live Robinhood MCP responses. Trimmed to the
# fields under test, but keys, nesting, and value formats are unmodified —
# earlier synthetic fixtures guessed all three wrong.
REAL_WATCHLISTS_RESPONSE = {
    "data": {
        "watchlists": [
            {
                "id": "REDACTEDC2-0000-0000-0000-000000000020",
                "display_name": "My First List",
                "icon_emoji": "⚡️",
                "owner_type": "custom",
                "item_count": 53,
            },  # note: no display_description key at all
            {
                "id": "REDACTEDC4-0000-0000-0000-000000000022",
                "display_name": "Basket: Magnificent 7 Index",
                "display_description": (
                    "Z64:eJxdzTkLwkAQhuH/MvW47OzslXSLRxpzgCKipBAvUpjGgELwv7tRi2A38DDf28Md"
                    "Urgdrm1zaY7ntpu4SdOezk9AeEDaQ7GZBUj3pIVKUAppvTWopBfENUK+WqzHyk47ZE+C"
                    "KWoI1XKsWhtCVhSvqFlZZn+cMDL5ODM857vip/4TJtKomIWxQ3i+DmNVHKetdN/wqppu"
                    "R0pKGSSywrj6hdBBSs5rLxNj5esNnzk7tA=="
                ),
                "icon_emoji": "🧺",
                "owner_type": "custom",
                "item_count": 7,
            },
            {
                "id": "REDACTEDC6-0000-0000-0000-000000000024",
                "display_name": "Test 250 Char Limit Basket",
                # Legacy uncompressed metadata still live in the account.
                "display_description": (
                    '{"s":"test-250","w":{"AAPL":[10,1,100],"MSFT":[10,1,200]}}'
                ),
                "icon_emoji": "🧪",
                "owner_type": "custom",
            },
        ]
    }
}

# A real dollar-based market order. Note quantity IS populated once filled,
# average_price differs from price, and created_at/last_transaction_at differ
# by two days with 5- and 3-digit fractional seconds respectively.
REAL_FILLED_ORDER = {
    "id": "REDACTEDC5-0000-0000-0000-000000000023",
    "symbol": "NVDA",
    "side": "buy",
    "type": "market",
    "state": "filled",
    "quantity": "0.048067",
    "cumulative_quantity": "0.048067",
    "price": "206.800000",
    "stop_price": None,
    "average_price": "208.040000",
    "fees": "0.000000",
    "dollar_based_amount": {"amount": "10.000000", "currency_code": "USD"},
    "placed_agent": "agentic",
    "created_at": "2026-07-25T03:04:08.24711Z",
    "last_transaction_at": "2026-07-27T13:30:01.764Z",
    "executions": [{
        "id": "6a675d59-a896-474d-a3ec-99246bb68ccb",
        "price": "208.040000",
        "quantity": "0.048067",
        "timestamp": "2026-07-27T13:30:01.764Z",
    }],
}


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

    def test_reconstruct_basket_positions(self):
        orders = [
            {
                "symbol": "WDC",
                "state": "filled",
                "side": "buy",
                "quantity": "10",
                "average_price": "50.00",
            },
            {
                "symbol": "WDC",
                "state": "filled",
                "side": "buy",
                "quantity": "10",
                "average_price": "60.00",
            },
            {
                "symbol": "WDC",
                "state": "filled",
                "side": "sell",
                "quantity": "5",
                "average_price": "70.00",
            },
            {
                "symbol": "STX",
                "state": "cancelled",
                "side": "buy",
                "quantity": "10",
                "average_price": "100.00",
            },
        ]

        holdings = reconstruct_basket_positions(orders, basket_symbols=["WDC", "STX"])

        self.assertIn("WDC", holdings)
        self.assertNotIn("STX", holdings)  # STX order was cancelled

        wdc = holdings["WDC"]
        # 10 shares @ 50 + 10 shares @ 60 = 20 shares total cost 1100 (avg 55).
        # Sell 5 shares @ 70 -> Remaining 15 shares @ avg cost 55 = total cost 825.
        self.assertEqual(wdc["shares"], 15.0)
        self.assertEqual(wdc["avg_cost"], 55.0)
        self.assertEqual(wdc["total_cost"], 825.0)

    def test_reconstruct_with_snapshot(self):
        snapshot = {
            "ts": "2026-07-24T00:00:00Z",
            "h": {
                "WDC": [10.0, 50.0],  # 10 shares @ $50.00
            },
        }

        orders = [
            # Old order before snapshot timestamp -> should be skipped
            {
                "symbol": "WDC",
                "state": "filled",
                "side": "buy",
                "quantity": "5",
                "average_price": "40.00",
                "created_at": "2026-07-23T12:00:00Z",
            },
            # New order after snapshot timestamp -> should be applied
            {
                "symbol": "WDC",
                "state": "filled",
                "side": "buy",
                "quantity": "10",
                "average_price": "60.00",
                "created_at": "2026-07-24T12:00:00Z",
            },
        ]

        holdings = reconstruct_basket_positions(orders, basket_symbols=["WDC"], snapshot=snapshot)

        self.assertIn("WDC", holdings)
        wdc = holdings["WDC"]
        # Snapshot: 10 @ 50 = $500 total cost
        # New buy: 10 @ 60 = $600 total cost
        # Combined: 20 shares, $1100 total cost -> avg_cost = $55.00
        self.assertEqual(wdc["shares"], 20.0)
        self.assertEqual(wdc["avg_cost"], 55.0)
        self.assertEqual(wdc["total_cost"], 1100.0)

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

    def test_encode_rejects_oversized_metadata(self):
        # 200 symbols cannot fit in Robinhood's 256-char description limit.
        target_weights = {f"SYM{i}": 0.5 for i in range(200)}
        snapshot = {"ts": 1721861640, "h": {sym: [1.25, 987.65] for sym in target_weights}}

        with self.assertRaises(ValueError) as ctx:
            encode_watchlist_metadata("oversized", target_weights, 5.0, snapshot=snapshot)
        self.assertIn("256", str(ctx.exception))


class TestOrderParsing(unittest.TestCase):
    """Regression tests for order fields Robinhood returns as null."""

    def test_dollar_amount_order_uses_cumulative_quantity(self):
        # Dollar-amount orders carry `quantity: null`; the real figure is in
        # cumulative_quantity. A dict.get() fallback misses this and drops the order.
        orders = [{
            "symbol": "WDC",
            "state": "filled",
            "side": "buy",
            "quantity": None,
            "cumulative_quantity": "2.5",
            "average_price": "50.00",
        }]

        holdings = reconstruct_basket_positions(orders, basket_symbols=["WDC"])

        self.assertIn("WDC", holdings)
        self.assertEqual(holdings["WDC"]["shares"], 2.5)
        self.assertEqual(holdings["WDC"]["avg_cost"], 50.0)

    def test_null_average_price_does_not_fall_back_to_limit_price(self):
        # `price` is the limit/reference price, not the fill price — in live data
        # the two differ. Booking cost basis at the limit price is silently wrong,
        # so an order with no fill price and no executions must be skipped instead.
        orders = [{
            "symbol": "WDC",
            "state": "filled",
            "side": "buy",
            "quantity": "4",
            "average_price": None,
            "price": "25.00",
        }]

        self.assertEqual(reconstruct_basket_positions(orders, basket_symbols=["WDC"]), {})

    def test_zero_quantity_order_is_skipped(self):
        orders = [{"symbol": "WDC", "state": "filled", "side": "buy", "quantity": None}]
        self.assertEqual(reconstruct_basket_positions(orders, basket_symbols=["WDC"]), {})

    def test_epoch_snapshot_ts_against_iso_orders(self):
        # Snapshot timestamps live as epoch ints inside the Z64 payload while orders
        # carry ISO strings — comparing them as strings raises TypeError.
        snapshot = {"ts": 1721779200, "h": {"WDC": [10.0, 50.0]}}  # 2024-07-24T00:00:00Z
        orders = [
            {
                "symbol": "WDC", "state": "filled", "side": "buy",
                "quantity": "5", "average_price": "40.00",
                "created_at": "2024-07-23T12:00:00Z",  # before snapshot -> skipped
            },
            {
                "symbol": "WDC", "state": "filled", "side": "buy",
                "quantity": "10", "average_price": "60.00",
                "created_at": "2024-07-24T12:00:00Z",  # after snapshot -> applied
            },
        ]

        holdings = reconstruct_basket_positions(orders, basket_symbols=["WDC"], snapshot=snapshot)
        self.assertEqual(holdings["WDC"]["shares"], 20.0)
        self.assertEqual(holdings["WDC"]["avg_cost"], 55.0)

    def test_variable_precision_fractional_seconds(self):
        # Robinhood emits both millisecond and microsecond precision; Python < 3.11
        # only accepts 3- or 6-digit fractions.
        snapshot = {"ts": "2024-07-24T00:00:00Z", "h": {}}
        for created_at in (
            "2024-07-23T23:59:59.9Z",      # before snapshot -> skipped
            "2024-07-23T23:59:59.99Z",
            "2024-07-23T23:59:59.999Z",
            "2024-07-23T23:59:59.999999Z",
        ):
            order = {
                "symbol": "WDC", "state": "filled", "side": "buy",
                "quantity": "5", "average_price": "40.00", "created_at": created_at,
            }
            holdings = reconstruct_basket_positions([order], ["WDC"], snapshot=snapshot)
            self.assertEqual(holdings, {}, f"{created_at} should predate the snapshot")

    def test_offset_timestamps_compare_correctly(self):
        # A '+00:00' snapshot vs a '-04:00' order must compare as instants, not strings.
        snapshot = {"ts": "2024-07-24T00:00:00+00:00", "h": {}}
        orders = [{
            "symbol": "WDC", "state": "filled", "side": "buy",
            "quantity": "5", "average_price": "40.00",
            "created_at": "2024-07-23T21:00:00-04:00",  # 2024-07-24T01:00:00Z -> applied
        }]

        holdings = reconstruct_basket_positions(orders, basket_symbols=["WDC"], snapshot=snapshot)
        self.assertEqual(holdings["WDC"]["shares"], 5.0)


class TestWatchlistParsing(unittest.TestCase):
    """Regression tests for the cloud-native watchlist path."""

    def setUp(self):
        self.desc = encode_watchlist_metadata(
            "storage-leaders",
            {"WDC": 50.0, "STX": 50.0},
            snapshot={"ts": 1721861640, "h": {"WDC": [10.0, 50.0]}},
        )

    def test_z64_watchlist_is_not_skipped(self):
        # Base64 payloads contain no '{', so any brace-based filter drops every
        # compressed basket — which is all of them.
        self.assertNotIn("{", self.desc)

        watchlists = [{"display_name": "Basket: Storage Leaders", "display_description": self.desc}]
        found = list(iter_watchlist_baskets(watchlists))

        self.assertEqual(len(found), 1)
        name, desc, metadata = found[0]
        self.assertEqual(name, "Basket: Storage Leaders")
        self.assertEqual(desc, self.desc)
        self.assertEqual(metadata["slug"], "storage-leaders")

    def test_non_basket_watchlists_are_skipped(self):
        watchlists = [
            {"display_name": "My Faves", "display_description": "stocks I like"},
            {"display_name": "No description"},
            {"display_name": "Empty", "display_description": None},
            {"display_name": "Basket: Storage Leaders", "display_description": self.desc},
        ]

        found = list(iter_watchlist_baskets(watchlists))
        self.assertEqual([name for name, _, _ in found], ["Basket: Storage Leaders"])

    def test_legacy_raw_json_watchlist_still_decodes(self):
        legacy = '{"s":"legacy-basket","w":{"WDC":[60],"STX":[40]}}'
        watchlists = [{"display_name": "Basket: Legacy", "display_description": legacy}]

        found = list(iter_watchlist_baskets(watchlists))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][2]["slug"], "legacy-basket")

    def test_parse_watchlists_json_accepts_array_and_envelope(self):
        payload = [{"display_name": "Basket: Storage Leaders", "display_description": self.desc}]

        self.assertEqual(parse_watchlists_json(json.dumps(payload)), payload)
        self.assertEqual(parse_watchlists_json(json.dumps({"watchlists": payload})), payload)

    def test_parse_watchlists_json_rejects_non_list(self):
        with self.assertRaises(ValueError):
            parse_watchlists_json('{"unexpected": "shape"}')

    def test_parse_watchlists_json_rejects_symbol_list(self):
        with self.assertRaises(ValueError):
            parse_watchlists_json('["AAPL", "MSFT"]')


class TestRealMCPShapes(unittest.TestCase):
    """Tests bound to payloads captured from the live Robinhood MCP server."""

    def test_parses_real_get_watchlists_envelope(self):
        # get_watchlists returns {"data": {"watchlists": [...]}} — double-nested.
        # Handling only a top-level "watchlists" key rejects the real response.
        parsed = parse_watchlists_json(json.dumps(REAL_WATCHLISTS_RESPONSE))
        self.assertEqual(len(parsed), 3)

        baskets = list(iter_watchlist_baskets(parsed))
        names = [name for name, _, _ in baskets]
        self.assertIn("Basket: Magnificent 7 Index", names)
        self.assertIn("Test 250 Char Limit Basket", names)   # legacy raw JSON
        self.assertNotIn("My First List", names)             # no description key

    def test_real_basket_metadata_decodes(self):
        _, desc, metadata = next(
            b for b in iter_watchlist_baskets(REAL_WATCHLISTS_RESPONSE["data"]["watchlists"])
            if b[0] == "Basket: Magnificent 7 Index"
        )
        self.assertEqual(metadata["slug"], "magnificent-7-index")
        self.assertEqual(len(metadata["weights"]), 7)
        self.assertIsNotNone(metadata["snapshot"])
        self.assertAlmostEqual(metadata["snapshot"]["h"]["NVDA"][0], 0.06865)

        # 240 of 256 with 7 symbols: capacity is nearly exhausted in production.
        self.assertLessEqual(len(desc), 256)
        self.assertGreater(len(desc), 200)

    def test_real_order_uses_fill_price_not_limit_price(self):
        # average_price 208.04 is the fill; price 206.80 is the limit/reference.
        holdings = reconstruct_basket_positions([REAL_FILLED_ORDER], ["NVDA"])
        self.assertAlmostEqual(holdings["NVDA"]["avg_cost"], 208.04, places=2)

    def test_real_order_filters_on_fill_time_not_creation(self):
        # Created 2026-07-25, filled 2026-07-27. A snapshot taken on the 26th
        # must still pick this order up — filtering on created_at loses it.
        snapshot = {"ts": "2026-07-26T00:00:00Z", "h": {}}
        holdings = reconstruct_basket_positions([REAL_FILLED_ORDER], ["NVDA"], snapshot=snapshot)
        self.assertIn("NVDA", holdings)
        self.assertAlmostEqual(holdings["NVDA"]["shares"], 0.048067)

        # A snapshot after the fill must exclude it.
        after = {"ts": "2026-07-28T00:00:00Z", "h": {}}
        self.assertEqual(reconstruct_basket_positions([REAL_FILLED_ORDER], ["NVDA"], after), {})

    def test_real_timestamp_precisions_parse(self):
        # Live data carries 2-, 3-, 5-, and 6-digit fractional seconds.
        for ts in ("2026-07-23T19:25:52.76Z", "2026-07-27T13:30:01.764Z",
                   "2026-07-25T03:04:08.24711Z", "2026-07-23T19:26:38.870797Z"):
            order = dict(REAL_FILLED_ORDER, last_transaction_at=ts)
            snapshot = {"ts": "2020-01-01T00:00:00Z", "h": {}}
            holdings = reconstruct_basket_positions([order], ["NVDA"], snapshot=snapshot)
            self.assertIn("NVDA", holdings, f"failed to parse {ts}")

    def test_price_falls_back_to_executions_not_limit_price(self):
        order = dict(REAL_FILLED_ORDER, average_price=None)
        holdings = reconstruct_basket_positions([order], ["NVDA"])
        # From executions (208.04), never the 206.80 limit price.
        self.assertAlmostEqual(holdings["NVDA"]["avg_cost"], 208.04, places=2)

    def test_order_with_no_usable_price_is_skipped(self):
        order = dict(REAL_FILLED_ORDER, average_price=None, executions=[])
        self.assertEqual(reconstruct_basket_positions([order], ["NVDA"]), {})


class TestSnapshotBoundary(unittest.TestCase):

    def test_baseline_ts_rounds_up_so_its_own_fill_is_not_replayed(self):
        desc = encode_watchlist_metadata("t", {"WDC": 100.0})
        fill_ts = 1721861640.7
        updated = update_basket_watchlist_baseline(desc, "WDC", "buy", 10.0, 50.0, timestamp=fill_ts)
        snapshot = decode_watchlist_metadata(updated)["snapshot"]

        order = {
            "symbol": "WDC", "state": "filled", "side": "buy",
            "cumulative_quantity": "10", "average_price": "50.00",
            "last_transaction_at": datetime.datetime.fromtimestamp(
                fill_ts, tz=datetime.timezone.utc).isoformat(),
        }
        replayed = reconstruct_basket_positions([order], ["WDC"], snapshot=snapshot)
        # Must stay at 10 shares / $500 — not 20 / $1000.
        self.assertEqual(replayed["WDC"]["shares"], 10.0)
        self.assertEqual(replayed["WDC"]["total_cost"], 500.0)

    def test_unparseable_snapshot_ts_raises_instead_of_replaying_everything(self):
        snapshot = {"ts": "2024-07-24 00:00:00 UTC", "h": {"WDC": [10.0, 50.0]}}
        order = {
            "symbol": "WDC", "state": "filled", "side": "buy",
            "cumulative_quantity": "10", "average_price": "50.00",
            "last_transaction_at": "2020-01-01T00:00:00Z",
        }
        with self.assertRaises(ValueError):
            reconstruct_basket_positions([order], ["WDC"], snapshot=snapshot)


if __name__ == "__main__":
    unittest.main()
