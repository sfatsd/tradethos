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


def order(order_id="o1", symbol="NVDA", side="buy", quantity="10",
          average_price="50.00", state="filled", price="49.00"):
    """Build an order in the shape that get_equity_orders returns."""
    return {
        "id": order_id, "symbol": symbol, "side": side, "state": state,
        "quantity": quantity, "cumulative_quantity": quantity,
        "price": price, "average_price": average_price,
        "dollar_based_amount": {"amount": "10.00", "currency_code": "USD"},
        "created_at": "2026-07-23T19:25:22.952062Z",
        "last_transaction_at": "2026-07-23T19:25:23.115Z",
        "executions": [{"price": average_price, "quantity": quantity}],
    }


def orders_response(*orders):
    return json.dumps({"data": {"orders": list(orders)}})


class TestRecordFills(CliTestCase):

    def setUp(self):
        CliTestCase.setUp(self)
        self.slug = self.make_basket(name="Trio", symbols="NVDA:50,MSFT:50")

    def record(self, response, ids, account="000000000", *extra):
        return self.run_cli("record-fills", self.slug, "--orders-json", response,
                            "--order-ids", ids, "--account", account, *extra)

    def test_records_one_order(self):
        code, out, err = self.record(orders_response(order()), "o1")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["recorded"], ["o1"])
        shown = self.run_cli("show", self.slug)[1]
        position = [h for h in shown["holdings"] if h["symbol"] == "NVDA"][0]["position"]
        self.assertEqual(position["shares"], 10.0)

    def test_reads_average_price_not_price(self):
        code, _, err = self.record(
            orders_response(order(average_price="208.04", price="206.80")), "o1")
        self.assertEqual(code, 0, err)
        shown = self.run_cli("show", self.slug)[1]
        position = [h for h in shown["holdings"] if h["symbol"] == "NVDA"][0]["position"]
        self.assertEqual(position["avg_cost"], 208.04)

    def test_records_only_the_listed_ids(self):
        response = orders_response(order(order_id="mine"),
                                   order(order_id="theirs", quantity="99"))
        code, out, err = self.record(response, "mine")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["recorded"], ["mine"])
        shown = self.run_cli("show", self.slug)[1]
        position = [h for h in shown["holdings"] if h["symbol"] == "NVDA"][0]["position"]
        self.assertEqual(position["shares"], 10.0)

    def test_reads_the_side_of_each_order(self):
        buy_then_sell = orders_response(
            order(order_id="b1", side="buy", quantity="10", average_price="50.00"),
            order(order_id="s1", side="sell", quantity="4", average_price="70.00"))
        code, out, err = self.record(buy_then_sell, "b1,s1")
        self.assertEqual(code, 0, err)
        shown = self.run_cli("show", self.slug)[1]
        position = [h for h in shown["holdings"] if h["symbol"] == "NVDA"][0]["position"]
        self.assertEqual(position["shares"], 6.0)
        self.assertEqual(position["realized_pnl"], 80.0)

    def test_a_repeated_call_changes_nothing(self):
        response = orders_response(order())
        self.record(response, "o1")
        code, out, err = self.record(response, "o1")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["already_recorded"], ["o1"])
        shown = self.run_cli("show", self.slug)[1]
        position = [h for h in shown["holdings"] if h["symbol"] == "NVDA"][0]["position"]
        self.assertEqual(position["shares"], 10.0)

    def test_an_id_in_another_basket_is_skipped(self):
        other = self.make_basket(name="Other", symbols="NVDA:100")
        self.record(orders_response(order()), "o1")
        code, _, err = self.run_cli("record-fills", other, "--orders-json",
                                    orders_response(order()), "--order-ids", "o1",
                                    "--account", "000000000")
        # Nothing else was in the batch, so nothing was recorded.
        self.assertEqual(code, 1)
        self.assertIn("ORDER_IN_OTHER_BASKET", err)

    def test_a_batch_keeps_its_good_fills_when_one_id_is_taken(self):
        other = self.make_basket(name="Other", symbols="NVDA:50,MSFT:50")
        self.record(orders_response(order(order_id="taken")), "taken")
        batch = orders_response(
            order(order_id="taken"),
            order(order_id="fresh", symbol="MSFT", quantity="4",
                  average_price="25.00"))
        code, out, err = self.run_cli(
            "record-fills", other, "--orders-json", batch,
            "--order-ids", "taken,fresh", "--account", "000000000")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["recorded"], ["fresh"])
        reasons = [s["reason"] for s in out["skipped"]]
        self.assertIn("ORDER_IN_OTHER_BASKET", reasons)

    def test_a_wrong_account_is_refused(self):
        code, _, err = self.record(orders_response(order()), "o1", "999999")
        self.assertEqual(code, 1)
        self.assertIn("ACCOUNT_MISMATCH", err)

    def test_a_mixed_batch_records_the_good_orders(self):
        self.record(orders_response(order(order_id="b1", quantity="10")), "b1")
        batch = orders_response(
            order(order_id="b2", symbol="MSFT", quantity="5", average_price="20.00"),
            order(order_id="s9", side="sell", quantity="999", average_price="60.00"))
        code, out, err = self.record(batch, "b2,s9")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["recorded"], ["b2"])
        self.assertEqual(len(out["skipped"]), 1)
        self.assertEqual(out["skipped"][0]["order_id"], "s9")

    def test_recording_no_order_exits_one(self):
        batch = orders_response(
            order(order_id="s9", side="sell", quantity="999", average_price="60.00"))
        code, _, err = self.record(batch, "s9")
        self.assertEqual(code, 1)
        self.assertIn("NOTHING_RECORDED", err)

    def test_an_unfilled_order_is_skipped_then_recorded_later(self):
        pending = orders_response(order(order_id="p1", state="confirmed"))
        code, out, _ = self.record(pending, "p1")
        self.assertEqual(code, 1)
        filled = orders_response(order(order_id="p1", state="filled"))
        code, out, err = self.record(filled, "p1")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["recorded"], ["p1"])

    def test_cap_at_held_records_the_held_shares(self):
        self.record(orders_response(order(order_id="b1", quantity="10")), "b1")
        big_sell = orders_response(
            order(order_id="s1", side="sell", quantity="25", average_price="70.00"))
        code, _, _ = self.record(big_sell, "s1")
        self.assertEqual(code, 1)
        code, out, err = self.record(big_sell, "s1", "000000000", "--cap-at-held")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["capped"][0]["recorded_shares"], 10.0)
        shown = self.run_cli("show", self.slug)[1]
        position = [h for h in shown["holdings"] if h["symbol"] == "NVDA"][0]["position"]
        # A fully sold-out holding still reports its realized P&L. Only
        # shares and avg_cost zero; position itself must not go null, or a
        # profit or loss vanishes from the one place a user checks a single
        # symbol (fixed in basket_store.snapshot_dict).
        self.assertIsNotNone(position)
        self.assertEqual(position["shares"], 0.0)
        self.assertEqual(position["realized_pnl"], 200.0)

    def test_a_second_sell_in_one_batch_is_blocked_by_the_first(self):
        self.record(orders_response(order(order_id="b1", quantity="10")), "b1")
        batch = orders_response(
            order(order_id="s1", side="sell", quantity="6", average_price="70.00"),
            order(order_id="s2", side="sell", quantity="6", average_price="70.00"))
        code, out, err = self.record(batch, "s1,s2")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["recorded"], ["s1"])
        self.assertEqual(len(out["skipped"]), 1)
        skipped = out["skipped"][0]
        self.assertEqual(skipped["order_id"], "s2")
        self.assertEqual(skipped["reason"], "OVERSELL")
        self.assertEqual(skipped["held"], 4.0)

    def test_cap_at_held_is_refused_for_more_than_one_id(self):
        response = orders_response(order(order_id="a"), order(order_id="b"))
        code, _, err = self.record(response, "a,b", "000000000", "--cap-at-held")
        self.assertEqual(code, 1)
        self.assertIn("CAP_NEEDS_ONE_ORDER", err)

    def test_an_unknown_id_is_reported(self):
        code, _, err = self.record(orders_response(order()), "missing")
        self.assertEqual(code, 1)
        self.assertIn("ORDER_NOT_IN_RESPONSE", err)

    def test_remove_holding_refuses_a_held_position(self):
        self.record(orders_response(order()), "o1")
        code, _, err = self.run_cli("remove-holding", self.slug, "--symbol", "NVDA")
        self.assertEqual(code, 1)
        self.assertIn("HOLDING_HAS_POSITION", err)

    def test_remove_holding_force_removes_a_held_position(self):
        self.record(orders_response(order()), "o1")
        code, _, err = self.run_cli("remove-holding", self.slug,
                                    "--symbol", "NVDA", "--force")
        self.assertEqual(code, 0, err)
        shown = self.run_cli("show", self.slug)[1]
        self.assertEqual([h["symbol"] for h in shown["holdings"]], ["MSFT"])
        self.assertEqual(shown["holdings"][0]["target_weight_pct"], 100)

    def test_delete_refuses_a_basket_that_holds_shares(self):
        self.record(orders_response(order()), "o1")
        code, _, err = self.run_cli("delete", self.slug)
        self.assertEqual(code, 1)
        self.assertIn("BASKET_HAS_POSITIONS", err)

    def test_delete_force_removes_the_basket(self):
        self.record(orders_response(order()), "o1")
        code, _, err = self.run_cli("delete", self.slug, "--force")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.run_cli("list")[1]["baskets"], [])

    def test_a_zero_target_weight_is_refused(self):
        code, _, err = self.run_cli("set-weight", self.slug, "--symbol", "NVDA",
                                    "--weight", "0")
        self.assertEqual(code, 1)
        self.assertIn("between 1 and 100", err)

    def test_a_negative_target_weight_is_refused(self):
        code, _, err = self.run_cli("set-weight", self.slug, "--symbol", "NVDA",
                                    "--weight", "-5")
        self.assertEqual(code, 1)
        self.assertIn("between 1 and 100", err)


if __name__ == "__main__":
    unittest.main()
