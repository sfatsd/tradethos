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
    attribute_orders,
    normalize_basket_weights,
    read_baskets,
    resolve_slug,
)

from basket_events import EventLog
from basket_store import replay

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


class TestResolveSlug(unittest.TestCase):

    def test_a_short_stored_slug_is_kept_even_though_it_differs(self):
        # ai-20 -> "Test 20 Symbol Compressed Basket" regenerates to
        # something completely different and much longer, but ai-20 is not
        # 24 characters, so it is never a truncation candidate and is kept.
        self.assertEqual(
            resolve_slug("ai-20", "Test 20 Symbol Compressed Basket"),
            "ai-20")

    def test_a_genuinely_truncated_slug_is_regenerated(self):
        # The old encoder spelled "&" out as "and"; slugify() drops it, so
        # the stored 24-char slug no longer looks like a literal prefix of
        # today's regenerated slug without reconstructing it the old way.
        self.assertEqual(
            resolve_slug("optical-and-photonics-in", "Optical & Photonics Index"),
            "optical-photonics-index")

    def test_a_24_char_slug_that_is_not_a_prefix_is_left_alone(self):
        # Same length as a real truncation, but unrelated to the display
        # name -- this must not be mistaken for a truncation.
        synthetic_old_slug = "totally-unrelated-slug-0"
        self.assertEqual(len(synthetic_old_slug), 24)
        self.assertEqual(
            resolve_slug(synthetic_old_slug, "Something Else Entirely"),
            synthetic_old_slug)

    def test_a_24_char_slug_that_was_never_truncated_is_kept(self):
        # storage-and-memory-index IS 24 characters whole -- its full
        # (un-truncated) form never exceeded the cap, so it must be kept
        # even though it is also exactly 24 characters long.
        self.assertEqual(
            resolve_slug("storage-and-memory-index", "Storage & Memory Index"),
            "storage-and-memory-index")

    def test_a_missing_slug_is_regenerated(self):
        self.assertEqual(resolve_slug("", "Brand New Basket"), "brand-new-basket")
        self.assertEqual(resolve_slug(None, "Brand New Basket"), "brand-new-basket")


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


class TestBuildAndMigrate(unittest.TestCase):

    ACCOUNT = "5PA00000"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.watchlists = load("live_watchlists.json")
        self.orders = load("live_orders.json")

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
        self.assertIn("optical-photonics-index", slugs)
        self.assertNotIn("optical-and-photonics-in", slugs)

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
        entry = [b for b in report["baskets"] if b["old_slug"] == "test-250"][0]
        self.assertTrue(entry["weights_changed"])

    def test_positions_come_from_order_data_not_the_snapshot(self):
        self.run_migration()
        mag7 = self.state()["magnificent-7-index"]
        nvda = mag7.holdings["NVDA"].position
        # The order filled 0.068659; the lossy snapshot said 0.06865.
        self.assertAlmostEqual(nvda.shares, 0.068659, places=9)
        self.assertAlmostEqual(nvda.avg_cost, 208.1299, places=4)

    def test_the_unattributed_order_is_in_no_basket(self):
        self.run_migration()
        total = 0.0
        for basket in self.state().values():
            holding = basket.holdings.get("NVDA")
            if holding:
                total += holding.position.shares
        self.assertAlmostEqual(total, 0.068659, places=9)

    def test_the_report_names_the_unattributed_order(self):
        report = self.run_migration()
        self.assertEqual(len(report["unattributed"]), 1)
        self.assertEqual(report["unattributed"][0]["symbol"], "NVDA")

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

    UNATTRIBUTED_ID = "REDACTEDC5-0000-0000-0000-000000000023"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.script = str(ROOT / "skills" / "basket-manager" / "scripts" / "migrate_v2.py")
        self.watchlists_text = (FIXTURES / "live_watchlists.json").read_text()
        self.orders_text = (FIXTURES / "live_orders.json").read_text()

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
        self.assertIn("NVDA", out)
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


if __name__ == "__main__":
    unittest.main()
