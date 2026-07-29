#!/usr/bin/env python3
"""Subprocess tests for the basket command-line tool.

These tests run the tool as a real process. The defect that the v0.2.2 review
found lived in main(), and unit tests alone did not reach it.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "skills" / "basket-manager" / "scripts" / "basket.py"


class CliTestCase(unittest.TestCase):
    """Base class that gives each test its own data directory."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        """Run the tool and return (exit_code, stdout_object, stderr_text)."""
        proc = subprocess.run(
            [sys.executable, str(CLI), "--data-dir", str(self.data_dir)] + list(args),
            capture_output=True, text=True,
        )
        payload = None
        if proc.stdout.strip():
            try:
                payload = json.loads(proc.stdout)
            except ValueError:
                payload = proc.stdout
        return proc.returncode, payload, proc.stderr

    def make_basket(self, name="Magnificent 7", symbols="NVDA:1,MSFT:1"):
        code, out, err = self.run_cli("create", name, "--symbols", symbols,
                                      "--account", "000000000")
        self.assertEqual(code, 0, err)
        return out["slug"]


class TestCreate(CliTestCase):

    def test_create_returns_the_normalized_weights(self):
        code, out, err = self.run_cli(
            "create", "Magnificent 7", "--symbols",
            "NVDA:1,MSFT:1,AAPL:1,GOOGL:1,AMZN:1,META:1,SPCX:1",
            "--account", "000000000")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["slug"], "magnificent-7")
        weights = dict((h["symbol"], h["target_weight_pct"]) for h in out["holdings"])
        self.assertEqual(sum(weights.values()), 100)
        self.assertEqual(weights["AAPL"], 15)
        self.assertEqual(weights["AMZN"], 15)
        self.assertEqual(weights["NVDA"], 14)

    def test_create_reports_the_weights_it_changed(self):
        code, out, _ = self.run_cli("create", "Trio", "--symbols", "A:1,B:1,C:1",
                                    "--account", "000000000")
        self.assertEqual(code, 0)
        self.assertTrue(out["normalized"])

    def test_create_writes_no_weight_changed_event(self):
        self.make_basket()
        code, out, _ = self.run_cli("history", "--format", "json")
        self.assertEqual(code, 0)
        types = [e["type"] for e in out["events"]]
        self.assertNotIn("weight_changed", types)

    def test_a_duplicate_slug_is_refused(self):
        self.make_basket()
        code, _, err = self.run_cli("create", "Magnificent 7", "--symbols",
                                    "NVDA:1", "--account", "000000000")
        self.assertEqual(code, 1)
        self.assertIn("SLUG_EXISTS", err)

    def test_a_zero_weight_is_refused(self):
        code, _, err = self.run_cli("create", "Bad", "--symbols", "NVDA:0,MSFT:1",
                                    "--account", "000000000")
        self.assertEqual(code, 1)
        self.assertIn("NVDA", err)

    def test_symbols_become_upper_case(self):
        code, out, _ = self.run_cli("create", "Lower", "--symbols", "nvda:1",
                                    "--account", "000000000")
        self.assertEqual(code, 0)
        self.assertEqual(out["holdings"][0]["symbol"], "NVDA")


class TestList(CliTestCase):

    def test_list_of_an_empty_store(self):
        code, out, err = self.run_cli("list")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["baskets"], [])

    def test_list_shows_each_basket(self):
        self.make_basket(name="One", symbols="A:1")
        self.make_basket(name="Two", symbols="B:1")
        code, out, _ = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertEqual(sorted(b["slug"] for b in out["baskets"]), ["one", "two"])

    def test_table_format_prints_text(self):
        self.make_basket(name="One", symbols="A:1")
        proc = subprocess.run(
            [sys.executable, str(CLI), "--data-dir", str(self.data_dir),
             "list", "--format", "table"],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("One", proc.stdout)


class TestShow(CliTestCase):

    def test_show_reports_targets_and_no_position(self):
        slug = self.make_basket()
        code, out, err = self.run_cli("show", slug)
        self.assertEqual(code, 0, err)
        self.assertEqual(out["totals"]["total_invested"], 0)
        self.assertIsNone(out["holdings"][0]["position"])

    def test_show_of_a_missing_basket_exits_one(self):
        code, _, err = self.run_cli("show", "nope")
        self.assertEqual(code, 1)
        self.assertIn("BASKET_NOT_FOUND", err)

    def test_show_with_prices_reports_value(self):
        slug = self.make_basket(symbols="NVDA:1")
        code, out, err = self.run_cli("show", slug, "--prices", '{"NVDA": 210.0}')
        self.assertEqual(code, 0, err)
        self.assertEqual(out["holdings"][0]["current_price"], 210.0)


class TestSnapshotExport(CliTestCase):

    def test_a_write_creates_the_snapshot_file(self):
        slug = self.make_basket()
        path = self.data_dir / "baskets" / (slug + ".json")
        self.assertTrue(path.exists())
        data = json.loads(path.read_text())
        self.assertEqual(data["slug"], slug)

    def test_deleting_every_snapshot_does_not_change_a_read(self):
        slug = self.make_basket()
        before = self.run_cli("show", slug)[1]
        for path in (self.data_dir / "baskets").glob("*.json"):
            path.unlink()
        after = self.run_cli("show", slug)[1]
        del before["totals"]["built_at"], after["totals"]["built_at"]
        self.assertEqual(before, after)

    def test_export_restores_the_files(self):
        slug = self.make_basket()
        for path in (self.data_dir / "baskets").glob("*.json"):
            path.unlink()
        code, _, err = self.run_cli("export")
        self.assertEqual(code, 0, err)
        self.assertTrue((self.data_dir / "baskets" / (slug + ".json")).exists())


class TestWeightCommands(CliTestCase):

    def three_holding_basket(self):
        code, out, err = self.run_cli(
            "create", "Trio", "--symbols", "NVDA:50,MSFT:30,AAPL:20",
            "--account", "000000000")
        self.assertEqual(code, 0, err)
        return out["slug"]

    def weights(self, slug):
        out = self.run_cli("show", slug)[1]
        return dict((h["symbol"], h["target_weight_pct"]) for h in out["holdings"])

    def test_set_weight_proportional_is_the_default(self):
        slug = self.three_holding_basket()
        code, out, err = self.run_cli("set-weight", slug, "--symbol", "NVDA",
                                      "--weight", "20")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.weights(slug), {"NVDA": 20, "MSFT": 48, "AAPL": 32})

    def test_set_weight_equal_fill(self):
        slug = self.three_holding_basket()
        code, _, err = self.run_cli("set-weight", slug, "--symbol", "NVDA",
                                    "--weight", "20", "--fill", "equal")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.weights(slug), {"NVDA": 20, "MSFT": 40, "AAPL": 40})

    def test_dry_run_writes_nothing(self):
        slug = self.three_holding_basket()
        before = self.run_cli("history", slug)[1]["events"]
        code, out, err = self.run_cli("set-weight", slug, "--symbol", "NVDA",
                                      "--weight", "20", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertTrue(out["dry_run"])
        after = self.run_cli("history", slug)[1]["events"]
        self.assertEqual(len(before), len(after))
        self.assertEqual(self.weights(slug)["NVDA"], 50)

    def test_dry_run_returns_the_complete_set(self):
        slug = self.three_holding_basket()
        out = self.run_cli("set-weight", slug, "--symbol", "NVDA",
                           "--weight", "20", "--dry-run")[1]
        self.assertEqual(out["weights"], {"NVDA": 20, "MSFT": 48, "AAPL": 32})

    def test_set_weights_needs_every_holding(self):
        slug = self.three_holding_basket()
        code, _, err = self.run_cli("set-weights", slug, "--weights", "NVDA:50,MSFT:50")
        self.assertEqual(code, 1)
        self.assertIn("AAPL", err)

    def test_set_weights_refuses_an_unknown_symbol(self):
        slug = self.three_holding_basket()
        code, _, err = self.run_cli(
            "set-weights", slug, "--weights", "NVDA:40,MSFT:30,AAPL:20,TSLA:10")
        self.assertEqual(code, 1)
        self.assertIn("TSLA", err)

    def test_set_weights_normalizes(self):
        slug = self.three_holding_basket()
        code, _, err = self.run_cli("set-weights", slug,
                                    "--weights", "NVDA:1,MSFT:1,AAPL:1")
        self.assertEqual(code, 0, err)
        self.assertEqual(sum(self.weights(slug).values()), 100)

    def test_set_weights_writes_only_changed_holdings(self):
        slug = self.three_holding_basket()
        self.run_cli("set-weights", slug, "--weights", "NVDA:50,MSFT:30,AAPL:20")
        events = self.run_cli("history", slug)[1]["events"]
        changed = [e for e in events if e["type"] == "weight_changed"]
        self.assertEqual(changed, [])

    def test_add_holding_scales_the_others_down(self):
        slug = self.three_holding_basket()
        code, _, err = self.run_cli("add-holding", slug, "--symbol", "TSLA",
                                    "--weight", "20")
        self.assertEqual(code, 0, err)
        weights = self.weights(slug)
        self.assertEqual(weights["TSLA"], 20)
        self.assertEqual(sum(weights.values()), 100)

    def test_remove_holding_scales_the_others_up(self):
        slug = self.three_holding_basket()
        code, _, err = self.run_cli("remove-holding", slug, "--symbol", "AAPL")
        self.assertEqual(code, 0, err)
        weights = self.weights(slug)
        self.assertNotIn("AAPL", weights)
        self.assertEqual(sum(weights.values()), 100)

    def test_every_command_leaves_the_total_at_one_hundred(self):
        slug = self.three_holding_basket()
        for args in (
            ("set-weight", slug, "--symbol", "NVDA", "--weight", "70"),
            ("add-holding", slug, "--symbol", "TSLA", "--weight", "10"),
            ("set-weights", slug, "--weights", "NVDA:1,MSFT:1,AAPL:1,TSLA:1"),
            ("remove-holding", slug, "--symbol", "TSLA"),
        ):
            code, _, err = self.run_cli(*args)
            self.assertEqual(code, 0, err)
            self.assertEqual(sum(self.weights(slug).values()), 100, args)

    def test_a_result_that_would_fall_to_zero_is_refused(self):
        slug = self.three_holding_basket()
        code, _, err = self.run_cli("set-weight", slug, "--symbol", "NVDA",
                                    "--weight", "99")
        self.assertEqual(code, 1)
        self.assertIn("1 percent cannot cover 2 holdings", err)

    def test_set_name_does_not_change_the_slug(self):
        slug = self.three_holding_basket()
        code, _, err = self.run_cli("set-name", slug, "--name", "Renamed")
        self.assertEqual(code, 0, err)
        out = self.run_cli("show", slug)[1]
        self.assertEqual(out["slug"], slug)
        self.assertEqual(out["name"], "Renamed")

    def test_remove_holding_proceeds_when_no_shares_are_held(self):
        # The refusal path needs a recorded fill, so Task 6 covers it.
        slug = self.three_holding_basket()
        code, _, err = self.run_cli("remove-holding", slug, "--symbol", "NVDA")
        self.assertEqual(code, 0, err)


if __name__ == "__main__":
    unittest.main()
