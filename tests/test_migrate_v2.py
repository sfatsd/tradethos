#!/usr/bin/env python3
"""Tests for the Stage 2 migration."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "basket-manager" / "scripts"))

from migrate_v2 import (
    SHARE_TOLERANCE,
    _build_basket_plans,
    attribute_orders,
    filter_orders_by_account,
    migrate,
    normalize_basket_weights,
    read_baskets,
    resolve_slug,
    weight_normalization_report,
)

from basket_events import EventLog
from basket_store import replay

FIXTURES = ROOT / "tests" / "fixtures"

# The fixtures are synthetic. tests/fixtures/generate_sample_fixtures.py
# builds them, documents every structural property they carry, and is the
# only way they should be changed.
WATCHLISTS = "sample_watchlists.json"
ORDERS = "sample_orders.json"


def load(name):
    return json.loads((FIXTURES / name).read_text())


class TestReadBaskets(unittest.TestCase):

    def setUp(self):
        self.baskets = read_baskets(load(WATCHLISTS))
        self.by_slug = dict((b["slug"], b) for b in self.baskets)

    def test_finds_every_basket_and_skips_ordinary_lists(self):
        # Nine watchlists; the three that are ordinary lists must not decode.
        self.assertEqual(len(load(WATCHLISTS)["data"]["watchlists"]), 9)
        self.assertEqual(len(self.baskets), 6)
        self.assertNotIn("long-term-watch", self.by_slug)
        self.assertNotIn("crypto-watch", self.by_slug)

    def test_reads_weights_and_snapshots(self):
        core = self.by_slug["core-growth-index"]
        self.assertEqual(len(core["weights"]), 7)
        self.assertEqual(len(core["snapshot"]), 7)

    def test_a_basket_without_a_snapshot_reads_as_definition_only(self):
        semis = self.by_slug["broad-semis-basket"]
        self.assertEqual(len(semis["weights"]), 10)
        self.assertEqual(semis["snapshot"], {})

    def test_a_basket_with_no_watchlist_items_still_reads(self):
        # sum-120-test and grid-20 have item_count 0 but carry full metadata,
        # so a basket must be read from its description, never from its size.
        zero_item_names = set(
            w["display_name"] for w in load(WATCHLISTS)["data"]["watchlists"]
            if w["item_count"] == 0 and w.get("display_description"))
        self.assertEqual(zero_item_names,
                         {"Basket: Sum 120 Test Basket",
                          "Basket: Twenty Symbol Compressed Basket"})
        self.assertEqual(len(self.by_slug["sum-120-test"]["weights"]), 12)
        self.assertEqual(len(self.by_slug["grid-20"]["weights"]), 20)

    def test_the_one_hundred_and_twenty_basket_sums_to_one_hundred_and_twenty(self):
        # This is what keeps normalization exercised end to end.
        weights = self.by_slug["sum-120-test"]["weights"]
        self.assertEqual(sum(weights.values()), 120.0)


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


class TestResolveSlug(unittest.TestCase):

    def test_a_short_stored_slug_is_kept_even_though_it_differs(self):
        # grid-20 -> "Twenty Symbol Compressed Basket" regenerates to
        # something completely different and much longer, but grid-20 is not
        # 24 characters, so it is never a truncation candidate and is kept.
        self.assertEqual(
            resolve_slug("grid-20", "Twenty Symbol Compressed Basket"),
            "grid-20")

    def test_a_genuinely_truncated_slug_is_regenerated(self):
        # The old encoder spelled "&" out as "and"; slugify() drops it, so
        # the stored 24-char slug no longer looks like a literal prefix of
        # today's regenerated slug without reconstructing it the old way.
        # "photonics-and-lasers-index" is 26 characters, so cutting it to 24
        # really did lose something.
        self.assertEqual(len("photonics-and-lasers-ind"), 24)
        self.assertEqual(
            resolve_slug("photonics-and-lasers-ind", "Photonics & Lasers Index"),
            "photonics-lasers-index")

    def test_a_24_char_slug_that_is_not_a_prefix_is_left_alone(self):
        # Same length as a real truncation, but unrelated to the display
        # name -- this must not be mistaken for a truncation.
        synthetic_old_slug = "totally-unrelated-slug-0"
        self.assertEqual(len(synthetic_old_slug), 24)
        self.assertEqual(
            resolve_slug(synthetic_old_slug, "Something Else Entirely"),
            synthetic_old_slug)

    def test_a_24_char_slug_that_was_never_truncated_is_kept(self):
        # optics-and-storage-index IS 24 characters whole -- its full
        # (un-truncated) form never exceeded the cap, so it must be kept
        # even though it is also exactly 24 characters long.
        self.assertEqual(len("optics-and-storage-index"), 24)
        self.assertEqual(
            resolve_slug("optics-and-storage-index", "Optics & Storage Index"),
            "optics-and-storage-index")

    def test_a_missing_slug_is_regenerated(self):
        self.assertEqual(resolve_slug("", "Brand New Basket"), "brand-new-basket")
        self.assertEqual(resolve_slug(None, "Brand New Basket"), "brand-new-basket")


class TestAttribution(unittest.TestCase):

    def setUp(self):
        self.baskets = read_baskets(load(WATCHLISTS))
        self.orders = load(ORDERS)["data"]["orders"]
        self.assignments, self.unattributed = attribute_orders(self.baskets, self.orders)

    def slug_for(self, symbol, shares):
        for order in self.orders:
            if order["symbol"] == symbol and abs(float(order["cumulative_quantity"]) - shares) < 1e-9:
                return self.assignments.get(order["id"])
        self.fail("no order for %s %s" % (symbol, shares))

    def test_lser_orders_split_across_two_baskets(self):
        # LSER is claimed by both baskets, 0.018 apart. 0.031206 answers
        # Photonics' 0.03120 claim; 0.013184 answers Optics & Storage's
        # 0.01320. Each order has to find its own basket.
        self.assertEqual(self.slug_for("LSER", 0.031206), "photonics-and-lasers-ind")
        self.assertEqual(self.slug_for("LSER", 0.013184), "optics-and-storage-index")

    def test_memx_goes_to_storage_because_broad_semis_claims_nothing(self):
        # MEMX sits in both broad-semis-basket and optics-and-storage-index,
        # but broad-semis has no snapshot, so it claims nothing and cannot win.
        self.assertEqual(self.slug_for("MEMX", 0.020896), "optics-and-storage-index")

    def test_the_basket_alfa_order_goes_to_core_growth(self):
        self.assertEqual(self.slug_for("ALFA", 0.071204), "core-growth-index")

    def test_the_standalone_alfa_order_is_unattributed(self):
        # Bought six days later, outside any basket. Leaving it out is correct.
        self.assertIsNone(self.slug_for("ALFA", 0.052140))
        ids = [o["id"] for o in self.unattributed]
        self.assertIn("aa000001-0000-4000-8000-000000000001", ids)

    def test_exactly_one_order_is_unattributed(self):
        self.assertEqual(len(self.unattributed), 1)

    def test_every_other_order_is_assigned(self):
        assigned = [o for o in self.orders if self.assignments.get(o["id"])]
        self.assertEqual(len(assigned), 19)

    def test_no_order_is_assigned_to_two_baskets(self):
        self.assertEqual(len(set(self.assignments)), len(self.assignments))

    def test_a_basket_without_a_snapshot_receives_no_orders(self):
        owned = [s for s in self.assignments.values() if s == "broad-semis-basket"]
        self.assertEqual(owned, [])

    def test_tolerance_absorbs_the_worst_drift(self):
        # PHOT is the widest gap in the fixture between a lossy 5-decimal
        # claim and the 6-decimal order that produced it: claimed 0.04780,
        # filled 0.047688, so 0.000112 apart. The tolerance must absorb it.
        self.assertGreater(SHARE_TOLERANCE, 0.000112)
        self.assertEqual(self.slug_for("PHOT", 0.047688), "photonics-and-lasers-ind")

    def test_tolerance_is_tight_enough_to_separate_competing_claims(self):
        # LSER's two claims differ by 0.018; the tolerance must be far below
        # that, or one order could satisfy either basket.
        self.assertLess(SHARE_TOLERANCE, 0.018 / 2)


class TestBuildAndMigrate(unittest.TestCase):

    ACCOUNT = "5PA00000"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.watchlists = load(WATCHLISTS)
        self.orders = load(ORDERS)

    def tearDown(self):
        self.tmp.cleanup()

    def run_migration(self, apply=True, account=None):
        from migrate_v2 import migrate
        return migrate(self.dir, self.watchlists, self.orders, None,
                        account if account is not None else self.ACCOUNT,
                        apply=apply)

    def state(self):
        return replay(EventLog(self.dir).read()).baskets

    def test_dry_run_writes_nothing(self):
        report = self.run_migration(apply=False)
        self.assertFalse((self.dir / "events.log.jsonl").exists())
        self.assertTrue(report["dry_run"])
        self.assertEqual(len(report["baskets"]), 6)

    def test_apply_creates_every_basket(self):
        self.run_migration()
        self.assertEqual(len(self.state()), 6)

    def test_the_migrated_baskets_carry_the_account(self):
        self.run_migration()
        for slug, basket in self.state().items():
            self.assertEqual(basket.account_number, self.ACCOUNT, slug)

    def test_the_truncated_slug_is_regenerated(self):
        self.run_migration()
        slugs = set(self.state())
        self.assertIn("photonics-lasers-index", slugs)
        self.assertNotIn("photonics-and-lasers-ind", slugs)

    def test_the_untruncated_24_char_slug_is_kept(self):
        self.run_migration()
        self.assertIn("optics-and-storage-index", set(self.state()))

    def test_weights_are_normalized_to_one_hundred(self):
        self.run_migration()
        for slug, basket in self.state().items():
            total = sum(h.target_weight_pct for h in basket.holdings.values())
            self.assertEqual(total, 100, slug)
            for holding in basket.holdings.values():
                self.assertEqual(holding.target_weight_pct,
                                 int(holding.target_weight_pct))

    def test_the_one_hundred_and_twenty_basket_is_reported_as_changed(self):
        report = self.run_migration()
        entry = [b for b in report["baskets"] if b["old_slug"] == "sum-120-test"][0]
        self.assertTrue(entry["weights_changed"])

    def test_positions_come_from_order_data_not_the_snapshot(self):
        self.run_migration()
        core = self.state()["core-growth-index"]
        alfa = core.holdings["ALFA"].position
        # The order filled 0.071204; the lossy snapshot said 0.07120. The
        # position must carry the order's figure, not the snapshot's.
        self.assertAlmostEqual(alfa.shares, 0.071204, places=9)
        self.assertAlmostEqual(alfa.avg_cost, 210.5501, places=4)

    def test_the_unattributed_order_is_in_no_basket(self):
        self.run_migration()
        total = 0.0
        for basket in self.state().values():
            holding = basket.holdings.get("ALFA")
            if holding and holding.has_position:
                total += holding.position.shares
        # Only the attributed 0.071204; the stray 0.052140 is nowhere.
        self.assertAlmostEqual(total, 0.071204, places=9)

    def test_the_report_names_the_unattributed_order(self):
        report = self.run_migration()
        self.assertEqual(len(report["unattributed"]), 1)
        self.assertEqual(report["unattributed"][0]["symbol"], "ALFA")
        self.assertAlmostEqual(report["unattributed"][0]["shares"], 0.052140,
                               places=9)

    def test_a_second_apply_changes_nothing(self):
        self.run_migration()
        before = EventLog(self.dir).count()
        self.run_migration()
        self.assertEqual(EventLog(self.dir).count(), before)

    def test_the_migration_writes_through_the_event_log(self):
        # Every line must be a valid event with a version field.
        self.run_migration()
        for event in EventLog(self.dir).read():
            self.assertIn("v", event)
            self.assertIn("type", event)
            self.assertIn("slug", event)


class TestMigrateCli(unittest.TestCase):
    """Drives migrate_v2.py as a subprocess, the way the controller will."""

    UNATTRIBUTED_ID = "aa000001-0000-4000-8000-000000000001"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.script = str(ROOT / "skills" / "basket-manager" / "scripts" / "migrate_v2.py")
        self.watchlists_text = (FIXTURES / WATCHLISTS).read_text()
        self.orders_text = (FIXTURES / ORDERS).read_text()

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *extra_args):
        cmd = [sys.executable, self.script,
               "--watchlists-json", self.watchlists_text,
               "--orders-json", self.orders_text,
               "--data-dir", str(self.dir),
               "--account", "5PA00000"]
        cmd.extend(extra_args)
        proc = subprocess.run(cmd, capture_output=True, text=True)
        return proc.returncode, proc.stdout, proc.stderr

    def test_dry_run_is_the_default(self):
        code, out, err = self.run_cli()
        self.assertEqual(code, 0)
        self.assertFalse((self.dir / "events.log.jsonl").exists())
        payload = json.loads(out)
        self.assertTrue(payload["dry_run"])
        self.assertEqual(len(payload["baskets"]), 6)

    def test_apply_writes(self):
        code, out, err = self.run_cli("--apply")
        self.assertEqual(code, 0)
        self.assertTrue((self.dir / "events.log.jsonl").exists())
        payload = json.loads(out)
        self.assertFalse(payload["dry_run"])
        self.assertEqual(len(replay(EventLog(self.dir).read()).baskets), 6)

    def test_a_second_apply_is_a_no_op(self):
        self.run_cli("--apply")
        before = EventLog(self.dir).count()
        code, out, err = self.run_cli("--apply")
        self.assertEqual(code, 0)
        self.assertEqual(EventLog(self.dir).count(), before)

    def test_table_format_names_the_unattributed_order(self):
        code, out, err = self.run_cli("--format", "table")
        self.assertEqual(code, 0)
        self.assertIn("DRY RUN", out)
        self.assertIn("ALFA", out)
        self.assertIn(self.UNATTRIBUTED_ID, out)

    def test_table_format_prints_the_dry_run_banner_only_once(self):
        code, out, err = self.run_cli("--format", "table")
        self.assertEqual(code, 0)
        self.assertEqual(out.count("DRY RUN"), 1)
        self.assertNotIn("DRY RUN", err)

    def test_a_missing_required_argument_exits_one_with_a_json_error(self):
        proc = subprocess.run(
            [sys.executable, self.script, "--data-dir", str(self.dir)],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertFalse((self.dir / "events.log.jsonl").exists())
        payload = json.loads(proc.stderr)
        self.assertIn("code", payload)
        self.assertIn("error", payload)

    def test_omitting_account_exits_non_zero(self):
        proc = subprocess.run(
            [sys.executable, self.script,
             "--watchlists-json", self.watchlists_text,
             "--orders-json", self.orders_text,
             "--data-dir", str(self.dir)],
            capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse((self.dir / "events.log.jsonl").exists())

    def test_invalid_json_exits_one_with_a_json_error(self):
        proc = subprocess.run(
            [sys.executable, self.script,
             "--watchlists-json", "not json",
             "--orders-json", self.orders_text,
             "--data-dir", str(self.dir),
             "--account", "5PA00000"],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        payload = json.loads(proc.stderr)
        self.assertIn("code", payload)


def basket_fixture(slug="test-basket", name="Test Basket", weights=None,
                    snapshot=None):
    """A minimal `read_baskets`-shaped basket dict for the tests below."""
    return {
        "slug": slug, "name": name,
        "weights": weights or {"NVDA": 100},
        "snapshot": snapshot or {},
        "threshold": 5.0,
    }


def synthetic_order(order_id, symbol="NVDA", side="buy", quantity="10",
                    average_price="200.00",
                    last_transaction_at="2026-01-01T00:00:00Z", **extra):
    order = {
        "id": order_id, "symbol": symbol, "side": side, "state": "filled",
        "cumulative_quantity": quantity, "average_price": average_price,
        "last_transaction_at": last_transaction_at,
    }
    order.update(extra)
    return order


def watchlist_entry(slug, name, weights_with_claims, ts=1782950000):
    """Build one `get_watchlists` result entry from {symbol: (weight, shares, avg_cost)}.

    Uses the raw-JSON metadata shape `decode_watchlist_metadata` accepts
    directly (no Z64/zlib wrapping needed for a test fixture).
    """
    w = dict((symbol, list(triple)) for symbol, triple in weights_with_claims.items())
    description = json.dumps({"s": slug, "w": w, "t": ts})
    return {"display_name": "Basket: %s" % name, "display_description": description}


class TestSellEventsAreRecordedAsSells(unittest.TestCase):
    """Finding 1: a filled sell must not be written to the log as a buy."""

    def test_a_filled_sell_produces_a_sell_event_with_the_right_shares_and_price(self):
        basket = basket_fixture(snapshot={"NVDA": [10.0, 200.0]})
        order = synthetic_order("sell-1", side="sell", quantity="10",
                                average_price="250.00")
        assignments, unattributed = attribute_orders([basket], [order])
        self.assertEqual(assignments.get("sell-1"), "test-basket")
        self.assertEqual(unattributed, [])

        plans = _build_basket_plans([basket], assignments, [order], {}, "ACCT1")
        trade_events = [e for e in plans[0]["events"] if e["type"] in ("buy", "sell")]
        self.assertEqual(len(trade_events), 1)
        event = trade_events[0]
        self.assertEqual(event["type"], "sell")
        self.assertEqual(event["shares"], 10.0)
        self.assertEqual(event["price"], 250.0)
        self.assertEqual(event["order_id"], "sell-1")

    def test_a_filled_buy_still_produces_a_buy_event(self):
        basket = basket_fixture(snapshot={"NVDA": [10.0, 200.0]})
        order = synthetic_order("buy-1", side="buy", quantity="10",
                                average_price="200.00")
        assignments, _ = attribute_orders([basket], [order])
        plans = _build_basket_plans([basket], assignments, [order], {}, "ACCT1")
        trade_events = [e for e in plans[0]["events"] if e["type"] in ("buy", "sell")]
        self.assertEqual(len(trade_events), 1)
        self.assertEqual(trade_events[0]["type"], "buy")

    def test_an_order_with_an_unrecognized_side_is_skipped_and_reported(self):
        basket = basket_fixture(snapshot={"NVDA": [10.0, 200.0]})
        order = synthetic_order("weird-1", side="exercise", quantity="10",
                                average_price="250.00")
        assignments, _ = attribute_orders([basket], [order])
        # It still matches the snapshot claim on shares alone -- attribution
        # never looks at side -- but its side is not one this tool can record.
        self.assertEqual(assignments.get("weird-1"), "test-basket")

        plans = _build_basket_plans([basket], assignments, [order], {}, "ACCT1")
        plan = plans[0]
        self.assertEqual(plan["orders"], [])
        self.assertFalse(any(e["type"] in ("buy", "sell") for e in plan["events"]))
        self.assertEqual(len(plan["skipped_orders"]), 1)
        self.assertEqual(plan["skipped_orders"][0]["order_id"], "weird-1")
        self.assertEqual(plan["skipped_orders"][0]["side"], "exercise")

    def test_the_migration_report_warns_about_the_unrecognized_side(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            watchlists = {"data": {"results": [
                watchlist_entry("test-basket", "Test Basket",
                                {"NVDA": (100, 10.0, 200.0)}),
            ]}}
            orders = {"data": {"orders": [
                synthetic_order("weird-1", side="exercise"),
            ]}}
            report = migrate(Path(tmp.name), watchlists, orders, None, "ACCT1",
                             apply=True)
            self.assertTrue(any("weird-1" in w for w in report["warnings"]))
            events = EventLog(Path(tmp.name)).read()
            self.assertFalse(any(e.get("order_id") == "weird-1" for e in events))
        finally:
            tmp.cleanup()


class TestClaimIsConsumedByAtMostOneOrder(unittest.TestCase):
    """The 'also worth addressing' item: a claim must not fund two orders."""

    def test_two_orders_matching_one_claim_attribute_only_one(self):
        basket = basket_fixture(snapshot={"NVDA": [10.0, 200.0]})
        order_a = synthetic_order("a", quantity="10", average_price="200.00")
        order_b = synthetic_order("b", quantity="10", average_price="210.00")
        assignments, unattributed = attribute_orders([basket], [order_a, order_b])
        self.assertEqual(len(assignments), 1)
        self.assertEqual(len(unattributed), 1)
        # The tie breaks on order id, so "a" wins and "b" is left unattributed.
        self.assertEqual(assignments.get("a"), "test-basket")
        self.assertIsNone(assignments.get("b"))


class TestAccountFiltering(unittest.TestCase):
    """Finding 4: an order from a different account must not be migrated."""

    def test_an_order_naming_a_different_account_is_excluded(self):
        matching, warnings = filter_orders_by_account(
            [synthetic_order("o1", account_number="OTHER-ACCT")], "ACCT1")
        self.assertEqual(matching, [])
        self.assertTrue(any("o1" in w and "OTHER-ACCT" in w for w in warnings))

    def test_an_order_naming_the_same_account_passes_through(self):
        order = synthetic_order("o1", account_number="ACCT1")
        matching, warnings = filter_orders_by_account([order], "ACCT1")
        self.assertEqual(matching, [order])
        self.assertEqual(warnings, [])

    def test_an_order_with_no_account_field_passes_through_unchecked(self):
        order = synthetic_order("o1")
        matching, warnings = filter_orders_by_account([order], "ACCT1")
        self.assertEqual(matching, [order])
        self.assertEqual(warnings, [])

    def test_the_migration_report_names_the_excluded_order(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            watchlists = {"data": {"results": [
                watchlist_entry("test-basket", "Test Basket",
                                {"NVDA": (100, 10.0, 200.0)}),
            ]}}
            orders = {"data": {"orders": [
                synthetic_order("other-acct", account_number="SOME-OTHER-ACCOUNT"),
            ]}}
            report = migrate(Path(tmp.name), watchlists, orders, None, "ACCT1",
                             apply=True)
            self.assertTrue(any("other-acct" in w for w in report["warnings"]))
            events = EventLog(Path(tmp.name)).read()
            self.assertFalse(any(e.get("order_id") == "other-acct" for e in events))
        finally:
            tmp.cleanup()


class TestNormalizedWeightReport(unittest.TestCase):
    """Finding 6: report each changed symbol's before-and-after weight."""

    def test_weight_normalization_report_carries_before_and_after(self):
        original = {"NVDA": 14.29, "MSFT": 14.29, "AAPL": 14.29, "GOOGL": 14.29,
                    "AMZN": 14.28, "META": 14.28, "SPCX": 14.28}
        normalized, _ = normalize_basket_weights(original)
        report = weight_normalization_report(original, normalized)
        self.assertEqual(set(report), set(original))
        self.assertEqual(report["NVDA"], {"from": 14.29, "to": 14})
        self.assertEqual(report["AAPL"], {"from": 14.29, "to": 15})

    def test_an_unchanged_weight_set_reports_no_symbols(self):
        original = {"A": 60, "B": 40}
        normalized, _ = normalize_basket_weights(original)
        self.assertEqual(weight_normalization_report(original, normalized), {})

    def test_the_migration_report_shows_core_growths_per_symbol_changes(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            report = migrate(Path(tmp.name), load(WATCHLISTS), load(ORDERS),
                             None, "5PA00000", apply=False)
        finally:
            tmp.cleanup()
        entry = [b for b in report["baskets"]
                if b["old_slug"] == "core-growth-index"][0]
        normalized = entry["normalized"]
        # Four weights of 14.29 and three of 14.28 sum to exactly 100.0 but
        # are not whole numbers. The two spare percent go to the largest
        # remainders, alphabetically: ALFA and BETA reach 15, the rest 14.
        self.assertEqual(set(normalized), set(
            ["ALFA", "BETA", "GAMA", "DLTA", "EPSI", "ZETA", "IOTA"]))
        self.assertAlmostEqual(normalized["ALFA"]["from"], 14.29)
        self.assertEqual(normalized["ALFA"]["to"], 15)
        self.assertAlmostEqual(normalized["BETA"]["from"], 14.29)
        self.assertEqual(normalized["BETA"]["to"], 15)
        self.assertAlmostEqual(normalized["GAMA"]["from"], 14.29)
        self.assertEqual(normalized["GAMA"]["to"], 14)
        self.assertAlmostEqual(normalized["IOTA"]["from"], 14.28)
        self.assertEqual(normalized["IOTA"]["to"], 14)


if __name__ == "__main__":
    unittest.main()
