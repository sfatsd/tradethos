#!/usr/bin/env python3
"""Unit tests for basket_store.py."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "basket-manager" / "scripts"))

from basket_events import make_event
from basket_store import Position, replay, slugify, snapshot_dict


def created(slug="mag7", name="Magnificent 7", **kw):
    return make_event("basket_created", slug, name=name,
                      description=kw.get("description", ""),
                      account_number=kw.get("account_number", "000000000"),
                      rebalance_threshold_pct=kw.get("threshold", 5.0),
                      ts=kw.get("ts", "2026-07-23T12:22:00Z"))


def holding(slug="mag7", symbol="NVDA", weight=50, thesis=""):
    return make_event("holding_added", slug, symbol=symbol, weight=weight,
                      thesis=thesis, ts="2026-07-23T12:22:01Z")


def buy(slug="mag7", symbol="NVDA", shares=10.0, price=50.0, order_id="o1",
        ts="2026-07-24T00:00:00Z", amount=None):
    return make_event("buy", slug, symbol=symbol, shares=shares, price=price,
                      amount=amount if amount is not None else shares * price,
                      order_id=order_id, ts=ts)


def sell(slug="mag7", symbol="NVDA", shares=5.0, price=70.0, order_id="o2",
         ts="2026-07-25T00:00:00Z"):
    return make_event("sell", slug, symbol=symbol, shares=shares, price=price,
                      amount=shares * price, order_id=order_id, ts=ts)


class TestSlugify(unittest.TestCase):

    def test_lowercases_and_hyphenates(self):
        self.assertEqual(slugify("Magnificent 7 Index"), "magnificent-7-index")

    def test_strips_punctuation(self):
        self.assertEqual(slugify("Storage & Memory"), "storage-memory")

    def test_collapses_repeated_separators(self):
        self.assertEqual(slugify("A   --  B"), "a-b")


class TestReplayDefinition(unittest.TestCase):

    def test_empty_log_gives_no_baskets(self):
        self.assertEqual(replay([]).baskets, {})

    def test_created_then_holdings(self):
        result = replay([created(), holding(symbol="NVDA", weight=60),
                         holding(symbol="MSFT", weight=40)])
        basket = result.baskets["mag7"]
        self.assertEqual(basket.name, "Magnificent 7")
        self.assertEqual(list(basket.holdings), ["NVDA", "MSFT"])
        self.assertEqual(basket.holdings["NVDA"].target_weight_pct, 60)

    def test_weight_changed(self):
        result = replay([created(), holding(symbol="NVDA", weight=60),
                         make_event("weight_changed", "mag7", symbol="NVDA",
                                    **{"from": 60, "to": 70})])
        self.assertEqual(result.baskets["mag7"].holdings["NVDA"].target_weight_pct, 70)

    def test_thesis_changed(self):
        result = replay([created(), holding(symbol="NVDA", thesis="old"),
                         make_event("thesis_changed", "mag7", symbol="NVDA",
                                    thesis="new")])
        self.assertEqual(result.baskets["mag7"].holdings["NVDA"].thesis, "new")

    def test_basket_updated(self):
        result = replay([created(),
                         make_event("basket_updated", "mag7",
                                    field="rebalance_threshold_pct",
                                    **{"from": 5.0, "to": 3.0})])
        self.assertEqual(result.baskets["mag7"].rebalance_threshold_pct, 3.0)

    def test_holding_removed(self):
        result = replay([created(), holding(symbol="NVDA"), holding(symbol="MSFT"),
                         make_event("holding_removed", "mag7", symbol="NVDA")])
        self.assertEqual(list(result.baskets["mag7"].holdings), ["MSFT"])

    def test_basket_deleted_removes_it_from_the_result(self):
        result = replay([created(), holding(), make_event("basket_deleted", "mag7")])
        self.assertNotIn("mag7", result.baskets)

    def test_a_slug_can_start_a_second_lifetime(self):
        events = [created(name="First"), holding(symbol="NVDA"),
                  make_event("basket_deleted", "mag7"),
                  created(name="Second", ts="2026-08-01T00:00:00Z")]
        basket = replay(events).baskets["mag7"]
        self.assertEqual(basket.name, "Second")
        self.assertEqual(list(basket.holdings), [])

    def test_an_event_after_deletion_without_recreation_is_ignored(self):
        events = [created(), make_event("basket_deleted", "mag7"),
                  holding(symbol="NVDA")]
        self.assertEqual(replay(events).baskets, {})

    def test_an_older_event_version_is_accepted(self):
        # The log keeps every line forever, so a reader must accept an old
        # shape. Section 9.5 of the design gives the rule.
        old_created = created()
        old_created["v"] = 0
        old_holding = holding(symbol="NVDA", weight=100)
        old_holding["v"] = 0
        basket = replay([old_created, old_holding]).baskets["mag7"]
        self.assertEqual(basket.holdings["NVDA"].target_weight_pct, 100)

    def test_an_unknown_event_type_is_ignored(self):
        # A future version can add an event type. An older reader must skip it
        # rather than fail.
        events = [created(), holding(symbol="NVDA", weight=100),
                  make_event("something_new", "mag7", detail="x")]
        basket = replay(events).baskets["mag7"]
        self.assertEqual(list(basket.holdings), ["NVDA"])

    def test_a_missing_optional_field_uses_a_default(self):
        # A new field must have a default, so an old line stays valid.
        bare = make_event("basket_created", "bare", name="Bare")
        basket = replay([bare]).baskets["bare"]
        self.assertEqual(basket.rebalance_threshold_pct, 5.0)
        self.assertEqual(basket.description, "")


class TestReplayMoney(unittest.TestCase):

    def test_a_single_buy(self):
        result = replay([created(), holding(), buy(shares=10.0, price=50.0)])
        position = result.baskets["mag7"].holdings["NVDA"].position
        self.assertEqual(position.shares, 10.0)
        self.assertEqual(position.avg_cost, 50.0)
        self.assertEqual(position.total_invested, 500.0)

    def test_average_cost_across_two_buys(self):
        result = replay([created(), holding(),
                         buy(shares=10.0, price=50.0, order_id="o1"),
                         buy(shares=10.0, price=60.0, order_id="o2")])
        position = result.baskets["mag7"].holdings["NVDA"].position
        self.assertEqual(position.shares, 20.0)
        self.assertEqual(position.avg_cost, 55.0)
        self.assertEqual(position.total_invested, 1100.0)

    def test_a_partial_sell(self):
        result = replay([created(), holding(),
                         buy(shares=10.0, price=50.0, order_id="o1"),
                         sell(shares=4.0, price=70.0, order_id="o2")])
        position = result.baskets["mag7"].holdings["NVDA"].position
        self.assertEqual(position.shares, 6.0)
        self.assertEqual(position.avg_cost, 50.0)
        self.assertEqual(position.realized_pnl, 80.0)

    def test_a_sell_of_all_shares(self):
        result = replay([created(), holding(),
                         buy(shares=10.0, price=50.0, order_id="o1"),
                         sell(shares=10.0, price=70.0, order_id="o2")])
        position = result.baskets["mag7"].holdings["NVDA"].position
        self.assertEqual(position.shares, 0.0)
        self.assertEqual(position.avg_cost, 0.0)
        self.assertEqual(position.realized_pnl, 200.0)

    def test_the_replay_uses_shares_times_price_and_ignores_amount(self):
        # A dollar order requests 10.00 and fills 0.048067 at 208.04, which is
        # 10.0006. Exactly one field can be authoritative.
        result = replay([created(), holding(),
                         buy(shares=0.048067, price=208.04, amount=10.00)])
        position = result.baskets["mag7"].holdings["NVDA"].position
        self.assertAlmostEqual(position.total_invested, 0.048067 * 208.04, places=6)

    def test_basket_totals_sum_the_holdings(self):
        events = [created(), holding(symbol="NVDA"), holding(symbol="MSFT"),
                  buy(symbol="NVDA", shares=10.0, price=50.0, order_id="o1"),
                  buy(symbol="MSFT", shares=2.0, price=100.0, order_id="o2")]
        basket = replay(events).baskets["mag7"]
        self.assertEqual(basket.total_invested, 700.0)

    def test_a_trade_for_an_unknown_symbol_is_ignored(self):
        result = replay([created(), holding(symbol="NVDA"),
                         buy(symbol="TSLA", order_id="o9")])
        self.assertNotIn("TSLA", result.baskets["mag7"].holdings)


class TestReplayDedupe(unittest.TestCase):

    def test_a_repeated_order_id_in_the_same_basket_is_ignored(self):
        result = replay([created(), holding(),
                         buy(shares=10.0, price=50.0, order_id="dup"),
                         buy(shares=10.0, price=50.0, order_id="dup")])
        position = result.baskets["mag7"].holdings["NVDA"].position
        self.assertEqual(position.shares, 10.0)
        self.assertEqual(len(result.ignored), 1)

    def test_an_ignored_event_is_reported_with_its_line_and_baskets(self):
        events = [created(slug="a", name="A"), holding(slug="a"),
                  created(slug="b", name="B"), holding(slug="b"),
                  buy(slug="a", order_id="dup"),
                  buy(slug="b", order_id="dup")]
        for number, event in enumerate(events, start=1):
            event["_line"] = number
        result = replay(events)
        self.assertEqual(len(result.ignored), 1)
        report = result.ignored[0]
        self.assertEqual(report["order_id"], "dup")
        self.assertEqual(report["slug"], "b")
        self.assertEqual(report["kept_by"], "a")
        self.assertEqual(report["line"], 6)

    def test_the_first_event_wins(self):
        result = replay([created(), holding(),
                         buy(shares=10.0, price=50.0, order_id="dup"),
                         buy(shares=99.0, price=1.0, order_id="dup")])
        self.assertEqual(result.baskets["mag7"].holdings["NVDA"].position.shares, 10.0)

    def test_the_order_index_maps_ids_to_slugs(self):
        result = replay([created(), holding(), buy(order_id="o1")])
        self.assertEqual(result.order_index["o1"], "mag7")


class TestSnapshotDict(unittest.TestCase):

    def test_shape_matches_the_spec(self):
        events = [created(), holding(symbol="NVDA", weight=100, thesis="GPU"),
                  buy(shares=10.0, price=50.0)]
        data = snapshot_dict(replay(events).baskets["mag7"])
        self.assertEqual(data["slug"], "mag7")
        self.assertEqual(data["schema_version"], 2)
        self.assertEqual(len(data["holdings"]), 1)
        self.assertEqual(data["holdings"][0]["symbol"], "NVDA")
        self.assertEqual(data["holdings"][0]["position"]["shares"], 10.0)
        self.assertEqual(data["totals"]["total_invested"], 500.0)
        self.assertIn("built_at", data["totals"])

    def test_a_holding_without_a_position_is_null(self):
        data = snapshot_dict(replay([created(), holding()]).baskets["mag7"])
        self.assertIsNone(data["holdings"][0]["position"])

    def test_a_fully_sold_holding_still_reports_its_realized_pnl(self):
        # Selling every share zeroes shares and avg_cost but must not hide
        # the accumulated profit or loss. The basket-level totals.realized_pnl
        # already carries it; the per-holding block must too.
        events = [created(), holding(),
                 buy(shares=10.0, price=50.0, order_id="o1"),
                 sell(shares=10.0, price=70.0, order_id="o2")]
        data = snapshot_dict(replay(events).baskets["mag7"])
        position = data["holdings"][0]["position"]
        self.assertIsNotNone(position)
        self.assertEqual(position["shares"], 0.0)
        self.assertEqual(position["avg_cost"], 0.0)
        self.assertEqual(position["realized_pnl"], 200.0)


class TestOversellClamping(unittest.TestCase):

    def test_position_sell_returns_the_uncovered_share_count(self):
        position = Position()
        position.buy(10.0, 50.0)
        oversold = position.sell(15.0, 70.0)
        self.assertEqual(oversold, 5.0)
        self.assertEqual(position.shares, 0.0)
        self.assertEqual(position.realized_pnl, 200.0)

    def test_position_sell_returns_zero_when_fully_covered(self):
        position = Position()
        position.buy(10.0, 50.0)
        oversold = position.sell(10.0, 70.0)
        self.assertEqual(oversold, 0.0)

    def test_replay_records_an_oversell_in_clamped(self):
        events = [created(), holding(),
                  buy(shares=10.0, price=50.0, order_id="o1"),
                  sell(shares=999.0, price=70.0, order_id="o2")]
        for number, event in enumerate(events, start=1):
            event["_line"] = number
        result = replay(events)
        self.assertEqual(len(result.clamped), 1)
        entry = result.clamped[0]
        self.assertEqual(entry["slug"], "mag7")
        self.assertEqual(entry["symbol"], "NVDA")
        self.assertEqual(entry["requested"], 999.0)
        self.assertEqual(entry["oversold"], 989.0)
        self.assertEqual(entry["line"], 4)

    def test_a_fully_covered_sell_leaves_clamped_empty(self):
        result = replay([created(), holding(),
                         buy(shares=10.0, price=50.0, order_id="o1"),
                         sell(shares=10.0, price=70.0, order_id="o2")])
        self.assertEqual(result.clamped, [])


if __name__ == "__main__":
    unittest.main()
