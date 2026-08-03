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
SCRIPTS = ROOT / "skills" / "basket-manager" / "scripts"
CLI = SCRIPTS / "basket.py"
sys.path.insert(0, str(SCRIPTS))

import basket            # noqa: E402  - needs the path above
import basket_events     # noqa: E402


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
                                      "--account", "123456789")
        self.assertEqual(code, 0, err)
        return out["slug"]


class TestCreate(CliTestCase):

    def test_create_returns_the_normalized_weights(self):
        code, out, err = self.run_cli(
            "create", "Magnificent 7", "--symbols",
            "NVDA:1,MSFT:1,AAPL:1,GOOGL:1,AMZN:1,META:1,SPCX:1",
            "--account", "123456789")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["slug"], "magnificent-7")
        weights = dict((h["symbol"], h["target_weight_pct"]) for h in out["holdings"])
        self.assertEqual(sum(weights.values()), 100)
        self.assertEqual(weights["AAPL"], 15)
        self.assertEqual(weights["AMZN"], 15)
        self.assertEqual(weights["NVDA"], 14)

    def test_create_reports_the_weights_it_changed(self):
        code, out, _ = self.run_cli("create", "Trio", "--symbols", "A:1,B:1,C:1",
                                    "--account", "123456789")
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
                                    "NVDA:1", "--account", "123456789")
        self.assertEqual(code, 1)
        self.assertIn("SLUG_EXISTS", err)

    def test_a_zero_weight_is_refused(self):
        code, _, err = self.run_cli("create", "Bad", "--symbols", "NVDA:0,MSFT:1",
                                    "--account", "123456789")
        self.assertEqual(code, 1)
        self.assertIn("NVDA", err)

    def test_symbols_become_upper_case(self):
        code, out, _ = self.run_cli("create", "Lower", "--symbols", "nvda:1",
                                    "--account", "123456789")
        self.assertEqual(code, 0)
        self.assertEqual(out["holdings"][0]["symbol"], "NVDA")

    def test_a_symbol_listed_twice_is_refused(self):
        # Silently keeping the last weight is a coin flip on which number the
        # user meant, so the tool refuses and names the symbol.
        code, _, err = self.run_cli("create", "Dup", "--symbols",
                                    "NVDA:10,NVDA:20", "--account", "123456789")
        self.assertEqual(code, 1)
        self.assertIn("DUPLICATE_SYMBOL", err)
        self.assertIn("NVDA", err)
        self.assertIn("10", err)
        self.assertIn("20", err)

    def test_a_symbol_listed_twice_in_different_cases_is_refused(self):
        # The symbols are upper-cased before the duplicate check, so these
        # are the same symbol.
        code, _, err = self.run_cli("create", "Dup", "--symbols",
                                    "nvda:10,NVDA:20", "--account", "123456789")
        self.assertEqual(code, 1)
        self.assertIn("DUPLICATE_SYMBOL", err)

    def test_a_duplicate_symbol_writes_nothing(self):
        self.run_cli("create", "Dup", "--symbols", "NVDA:10,NVDA:20",
                     "--account", "123456789")
        code, out, _ = self.run_cli("list")
        self.assertEqual(out["baskets"], [])


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

    def test_exporting_everything_removes_a_stale_snapshot(self):
        # A deleted basket's snapshot used to sit in the directory forever,
        # describing a basket the log no longer produces.
        keep = self.make_basket(name="Keeper", symbols="NVDA:1,MSFT:1")
        goner = self.make_basket(name="Goner", symbols="AMD:1,INTC:1")
        goner_path = self.data_dir / "baskets" / (goner + ".json")
        self.assertTrue(goner_path.exists())

        code, _, err = self.run_cli("delete", goner)
        self.assertEqual(code, 0, err)

        code, out, err = self.run_cli("export")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["exported"], [keep])
        self.assertFalse(goner_path.exists())
        self.assertTrue((self.data_dir / "baskets" / (keep + ".json")).exists())

    def test_exporting_everything_removes_an_unrelated_orphan_file(self):
        keep = self.make_basket(name="Keeper", symbols="NVDA:1,MSFT:1")
        orphan = self.data_dir / "baskets" / "never-existed.json"
        orphan.write_text('{"slug": "never-existed"}\n')

        code, _, err = self.run_cli("export")
        self.assertEqual(code, 0, err)
        self.assertFalse(orphan.exists())
        self.assertTrue((self.data_dir / "baskets" / (keep + ".json")).exists())

    def test_exporting_one_slug_deletes_nothing(self):
        keep = self.make_basket(name="Keeper", symbols="NVDA:1,MSFT:1")
        orphan = self.data_dir / "baskets" / "never-existed.json"
        orphan.write_text('{"slug": "never-existed"}\n')

        code, out, err = self.run_cli("export", keep)
        self.assertEqual(code, 0, err)
        self.assertEqual(out["exported"], [keep])
        # A single-slug export has not been asked about the rest of the
        # directory, and `Store.write` uses that form after every command.
        self.assertTrue(orphan.exists())

    def test_a_normal_write_leaves_another_baskets_snapshot_alone(self):
        first = self.make_basket(name="First", symbols="NVDA:1,MSFT:1")
        second = self.make_basket(name="Second", symbols="AMD:1,INTC:1")
        code, _, err = self.run_cli("set-weight", first, "--symbol", "NVDA",
                                    "--weight", "70")
        self.assertEqual(code, 0, err)
        self.assertTrue((self.data_dir / "baskets" / (second + ".json")).exists())


class TestWeightCommands(CliTestCase):

    def three_holding_basket(self):
        code, out, err = self.run_cli(
            "create", "Trio", "--symbols", "NVDA:50,MSFT:30,AAPL:20",
            "--account", "123456789")
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
          average_price="50.00", state="filled", price="49.00",
          created_at="2026-07-23T19:25:22.952062Z",
          last_transaction_at="2026-07-23T19:25:23.115Z"):
    """Build an order in the shape that get_equity_orders returns.

    A None timestamp drops that key, which is how an order with no usable
    fill time is built.
    """
    built = {
        "id": order_id, "symbol": symbol, "side": side, "state": state,
        "quantity": quantity, "cumulative_quantity": quantity,
        "price": price, "average_price": average_price,
        "dollar_based_amount": {"amount": "10.00", "currency_code": "USD"},
        "created_at": created_at,
        "last_transaction_at": last_transaction_at,
        "executions": [{"price": average_price, "quantity": quantity}],
    }
    return dict((k, v) for k, v in built.items() if v is not None)


def orders_response(*orders):
    return json.dumps({"data": {"orders": list(orders)}})


class TestRecordFills(CliTestCase):

    def setUp(self):
        CliTestCase.setUp(self)
        self.slug = self.make_basket(name="Trio", symbols="NVDA:50,MSFT:50")

    def record(self, response, ids, account="123456789", *extra):
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
                                    "--account", "123456789")
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
            "--order-ids", "taken,fresh", "--account", "123456789")
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
        code, out, err = self.record(big_sell, "s1", "123456789", "--cap-at-held")
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
        code, _, err = self.record(response, "a,b", "123456789", "--cap-at-held")
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

    def events_log(self):
        path = self.data_dir / "events.log.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()
                if line.strip()]

    def test_the_executions_timestamp_is_the_third_fill_time_source(self):
        # The order carries no top-level time, so the only usable value is
        # inside executions[]. A real response always has one there.
        built = order(order_id="exec_ts", quantity="10",
                      average_price="10.00",
                      created_at=None, last_transaction_at=None)
        built["executions"] = [{"price": "10.00", "quantity": "10",
                                "timestamp": "2026-01-05T15:00:00Z"}]
        code, out, err = self.run_cli(
            "record-fills", self.slug, "--orders-json",
            orders_response(built),
            "--order-ids", "exec_ts", "--account", "123456789")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["recorded"], ["exec_ts"])
        buys = [e for e in self.events_log() if e.get("order_id") == "exec_ts"]
        self.assertEqual(len(buys), 1)
        self.assertEqual(buys[0]["ts"], "2026-01-05T15:00:00Z")

    def test_a_top_level_time_beats_the_executions_timestamp(self):
        built = order(order_id="both", quantity="10", average_price="10.00",
                      created_at="2026-01-02T15:00:00Z",
                      last_transaction_at="2026-01-03T15:00:00Z")
        built["executions"] = [{"price": "10.00", "quantity": "10",
                                "timestamp": "2026-01-09T15:00:00Z"}]
        code, out, err = self.run_cli(
            "record-fills", self.slug, "--orders-json",
            orders_response(built),
            "--order-ids", "both", "--account", "123456789")
        self.assertEqual(code, 0, err)
        buys = [e for e in self.events_log() if e.get("order_id") == "both"]
        self.assertEqual(buys[0]["ts"], "2026-01-03T15:00:00Z")

    def test_a_filled_order_with_no_fill_time_aborts_the_call(self):
        built = order(order_id="notime", quantity="10", average_price="10.00",
                      created_at=None, last_transaction_at=None)
        built["executions"] = []
        code, out, err = self.run_cli(
            "record-fills", self.slug, "--orders-json",
            orders_response(built),
            "--order-ids", "notime", "--account", "123456789")
        self.assertEqual(code, 1)
        self.assertIn("MISSING_REQUIRED_FIELDS", err)
        self.assertIn("fill_time", err)

    def test_an_unparseable_fill_time_aborts_the_call(self):
        built = order(order_id="junk", quantity="10", average_price="10.00",
                      created_at="not a date",
                      last_transaction_at="not a date")
        built["executions"] = []
        code, out, err = self.run_cli(
            "record-fills", self.slug, "--orders-json",
            orders_response(built),
            "--order-ids", "junk", "--account", "123456789")
        self.assertEqual(code, 1)
        self.assertIn("MISSING_REQUIRED_FIELDS", err)

    def test_the_error_names_every_bad_order_and_every_gap(self):
        first = order(order_id="bad1", quantity="10", average_price="10.00",
                      created_at=None, last_transaction_at=None)
        first["executions"] = []
        second = order(order_id="bad2", symbol="MSFT", quantity="10",
                       average_price="10.00")
        del second["side"]
        code, out, err = self.run_cli(
            "record-fills", self.slug, "--orders-json",
            orders_response(first, second),
            "--order-ids", "bad1,bad2", "--account", "123456789")
        self.assertEqual(code, 1)
        self.assertIn("bad1", err)
        self.assertIn("bad2", err)
        self.assertIn("fill_time", err)
        self.assertIn("side", err)

    def test_one_bad_order_stops_the_good_orders_too(self):
        good = order(order_id="goodone", symbol="NVDA", quantity="10",
                     average_price="10.00")
        bad = order(order_id="badone", symbol="MSFT", quantity="10",
                    average_price="10.00",
                    created_at=None, last_transaction_at=None)
        bad["executions"] = []
        code, out, err = self.run_cli(
            "record-fills", self.slug, "--orders-json",
            orders_response(good, bad),
            "--order-ids", "goodone,badone", "--account", "123456789")
        self.assertEqual(code, 1)
        self.assertNotIn("goodone", (self.data_dir / "events.log.jsonl").read_text())

    def test_a_missing_symbol_id_or_state_aborts_the_call(self):
        no_symbol = order(order_id="nosym", quantity="10",
                          average_price="10.00")
        del no_symbol["symbol"]
        code, out, err = self.run_cli(
            "record-fills", self.slug, "--orders-json",
            orders_response(no_symbol),
            "--order-ids", "nosym", "--account", "123456789")
        self.assertEqual(code, 1)
        self.assertIn("MISSING_REQUIRED_FIELDS", err)
        self.assertIn("symbol", err)

        no_state = order(order_id="nostate", quantity="10",
                         average_price="10.00")
        del no_state["state"]
        code, out, err = self.run_cli(
            "record-fills", self.slug, "--orders-json",
            orders_response(no_state),
            "--order-ids", "nostate", "--account", "123456789")
        self.assertEqual(code, 1)
        self.assertIn("state", err)

    def test_the_log_is_unchanged_after_an_abort(self):
        log = self.data_dir / "events.log.jsonl"
        before = log.read_bytes()
        built = order(order_id="notime", quantity="10", average_price="10.00",
                      created_at=None, last_transaction_at=None)
        built["executions"] = []
        code, out, err = self.run_cli(
            "record-fills", self.slug, "--orders-json",
            orders_response(built),
            "--order-ids", "notime", "--account", "123456789")
        self.assertEqual(code, 1)
        self.assertEqual(log.read_bytes(), before)

    def test_a_missing_side_aborts_instead_of_skipping(self):
        built = order(order_id="noside", quantity="10", average_price="10.00")
        del built["side"]
        code, out, err = self.run_cli(
            "record-fills", self.slug, "--orders-json",
            orders_response(built),
            "--order-ids", "noside", "--account", "123456789")
        self.assertEqual(code, 1)
        self.assertIn("MISSING_REQUIRED_FIELDS", err)
        self.assertNotIn("UNKNOWN_SIDE", err)

    def test_an_unknown_side_value_aborts(self):
        code, out, err = self.run_cli(
            "record-fills", self.slug, "--orders-json",
            orders_response(order(order_id="weird", side="transfer",
                                  quantity="10", average_price="10.00")),
            "--order-ids", "weird", "--account", "123456789")
        self.assertEqual(code, 1)
        self.assertIn("MISSING_REQUIRED_FIELDS", err)

    def test_no_price_anywhere_aborts_instead_of_skipping(self):
        built = order(order_id="noprice", quantity="10", average_price=None)
        built["executions"] = []
        code, out, err = self.run_cli(
            "record-fills", self.slug, "--orders-json",
            orders_response(built),
            "--order-ids", "noprice", "--account", "123456789")
        self.assertEqual(code, 1)
        self.assertIn("MISSING_REQUIRED_FIELDS", err)
        self.assertNotIn("NO_SHARES_OR_PRICE", err)

    def test_the_executions_supply_the_price_when_average_price_is_absent(self):
        built = order(order_id="execprice", quantity="10", average_price=None)
        built["executions"] = [{"price": "12.00", "quantity": "10",
                                "timestamp": "2026-01-05T15:00:00Z"}]
        code, out, err = self.run_cli(
            "record-fills", self.slug, "--orders-json",
            orders_response(built),
            "--order-ids", "execprice", "--account", "123456789")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["recorded"], ["execprice"])

    def test_quantity_stands_in_for_a_missing_cumulative_quantity(self):
        built = order(order_id="qty", quantity="10", average_price="10.00")
        del built["cumulative_quantity"]
        code, out, err = self.run_cli(
            "record-fills", self.slug, "--orders-json",
            orders_response(built),
            "--order-ids", "qty", "--account", "123456789")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["recorded"], ["qty"])

    def test_an_open_order_is_skipped_and_does_not_abort(self):
        # The documented retry flow: record an open limit order, get
        # NOT_FILLED, call again once it fills. An open order has no price
        # and no fill time, and that data is correct.
        built = order(order_id="openone", state="confirmed", quantity="10",
                      average_price=None,
                      created_at=None, last_transaction_at=None)
        built["executions"] = []
        code, out, err = self.run_cli(
            "record-fills", self.slug, "--orders-json",
            orders_response(built, order(order_id="done", quantity="10",
                                         average_price="10.00")),
            "--order-ids", "openone,done", "--account", "123456789")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["recorded"], ["done"])
        self.assertEqual(out["skipped"][0]["reason"], "NOT_FILLED")

    def test_the_result_has_no_undated_key(self):
        code, out, err = self.run_cli(
            "record-fills", self.slug, "--orders-json",
            orders_response(order(order_id="o1", quantity="10",
                                  average_price="10.00")),
            "--order-ids", "o1", "--account", "123456789")
        self.assertEqual(code, 0, err)
        self.assertNotIn("undated", out)

    def test_an_id_absent_from_the_response_is_skipped_not_aborted(self):
        code, out, err = self.run_cli(
            "record-fills", self.slug, "--orders-json",
            orders_response(order(order_id="here", quantity="10",
                                  average_price="10.00")),
            "--order-ids", "here,ghost", "--account", "123456789")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["recorded"], ["here"])
        reasons = [s["reason"] for s in out["skipped"]]
        self.assertIn("ORDER_NOT_IN_RESPONSE", reasons)


class TestFillOrdering(CliTestCase):
    """record-fills must apply orders in trade order, not argument order.

    Average cost and realized profit and loss depend on the sequence. A buy at
    10, a sell at 15 and a buy at 20 give 2000.00 invested and 500.00 realized
    in trade order, but 1500.00 and 0.00 if the two buys are applied first.
    get_equity_orders returns orders newest first, so an agent forwarding ids
    in response order hits exactly that case.
    """

    def setUp(self):
        CliTestCase.setUp(self)
        self.slug = self.make_basket(name="Solo", symbols="NVDA:100")

    def batch(self):
        return orders_response(
            order(order_id="b1", side="buy", quantity="100",
                  average_price="10.00",
                  created_at="2026-01-01T15:00:00.12Z",
                  last_transaction_at="2026-01-01T15:00:00.12Z"),
            order(order_id="s1", side="sell", quantity="100",
                  average_price="15.00",
                  created_at="2026-01-02T15:00:00.123456Z",
                  last_transaction_at="2026-01-02T15:00:00.123456Z"),
            order(order_id="b2", side="buy", quantity="100",
                  average_price="20.00",
                  created_at="2026-01-03T15:00:00Z",
                  last_transaction_at="2026-01-03T15:00:00Z"))

    def position(self, slug=None):
        shown = self.run_cli("show", slug or self.slug)[1]
        return [h for h in shown["holdings"]
                if h["symbol"] == "NVDA"][0]["position"]

    def test_the_argument_order_does_not_change_the_result(self):
        code, out, err = self.run_cli(
            "record-fills", self.slug, "--orders-json", self.batch(),
            "--order-ids", "b1,b2,s1", "--account", "123456789")
        self.assertEqual(code, 0, err)
        # Sorted into trade order before any event was built.
        self.assertEqual(out["recorded"], ["b1", "s1", "b2"])
        position = self.position()
        self.assertEqual(position["total_invested"], 2000.00)
        self.assertEqual(position["realized_pnl"], 500.00)

    def test_newest_first_gives_the_same_state_as_oldest_first(self):
        # Each ordering gets its own empty store, so the two runs cannot see
        # each other's events.
        states = []
        saved = self.data_dir
        for ids in ("b1,s1,b2", "b2,s1,b1"):
            fresh = tempfile.TemporaryDirectory()
            self.addCleanup(fresh.cleanup)
            self.data_dir = Path(fresh.name)
            try:
                slug = self.make_basket(name="Solo", symbols="NVDA:100")
                code, _, err = self.run_cli(
                    "record-fills", slug, "--orders-json", self.batch(),
                    "--order-ids", ids, "--account", "123456789")
                self.assertEqual(code, 0, err)
                states.append(self.position(slug))
            finally:
                self.data_dir = saved
        self.assertEqual(states[0], states[1])
        self.assertEqual(states[0]["realized_pnl"], 500.00)
        self.assertEqual(states[0]["total_invested"], 2000.00)

    def test_a_fill_older_than_the_history_is_reported(self):
        # Record the January 3 buy first, then hand it the January 1 buy. The
        # tool records it - dropping a real fill is worse - but says so.
        code, _, err = self.run_cli(
            "record-fills", self.slug, "--orders-json", self.batch(),
            "--order-ids", "b2", "--account", "123456789")
        self.assertEqual(code, 0, err)

        code, out, err = self.run_cli(
            "record-fills", self.slug, "--orders-json", self.batch(),
            "--order-ids", "b1", "--account", "123456789")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["recorded"], ["b1"])
        self.assertEqual(len(out["late_fills"]), 1)
        late = out["late_fills"][0]
        self.assertEqual(late["order_id"], "b1")
        self.assertEqual(late["symbol"], "NVDA")
        self.assertEqual(late["latest_recorded"], "2026-01-03T15:00:00Z")

    def test_a_fill_in_sequence_is_not_reported_late(self):
        code, out, err = self.run_cli(
            "record-fills", self.slug, "--orders-json", self.batch(),
            "--order-ids", "b1,s1,b2", "--account", "123456789")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["late_fills"], [])

    def test_an_unreadable_fill_time_falls_back_to_created_at(self):
        # A usable time in created_at beats an unusable last_transaction_at,
        # rather than dumping the order at the end of the batch.
        batch = orders_response(
            order(order_id="early", side="buy", quantity="100",
                  average_price="10.00",
                  created_at="2026-01-01T15:00:00Z",
                  last_transaction_at="garbage"),
            order(order_id="later", side="sell", quantity="100",
                  average_price="15.00",
                  created_at="2026-01-02T15:00:00Z",
                  last_transaction_at="2026-01-02T15:00:00Z"))
        code, out, err = self.run_cli(
            "record-fills", self.slug, "--orders-json", batch,
            "--order-ids", "later,early", "--account", "123456789")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["recorded"], ["early", "later"])
        self.assertEqual(self.position()["realized_pnl"], 500.00)

    def test_a_repeated_id_in_one_batch_writes_one_event(self):
        response = orders_response(order(order_id="o1"))
        code, out, err = self.run_cli(
            "record-fills", self.slug, "--orders-json", response,
            "--order-ids", "o1,o1", "--account", "123456789")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["recorded"], ["o1"])
        self.assertEqual(out["repeated_ids"], ["o1"])
        # The replay dedupes, so the state was never wrong. The log is what
        # the repeat polluted, and the log is permanent.
        events = self.run_cli("history", self.slug)[1]["events"]
        buys = [e for e in events if e["type"] == "buy"]
        self.assertEqual(len(buys), 1)


class TestCorruptLineDegrades(CliTestCase):
    """One bad line must degrade one basket, not the whole store."""

    def setUp(self):
        CliTestCase.setUp(self)
        self.clean = self.make_basket(name="Clean", symbols="NVDA:100")
        self.other = self.make_basket(name="Other", symbols="MSFT:100")
        self.log = self.data_dir / "events.log.jsonl"

    def corrupt(self):
        """Append a truncated line, as a crash mid-flush leaves behind."""
        with self.log.open("a") as handle:
            handle.write('{"v":1,"ts":"2026-01-01T00:00:00Z","type":"buy"\n')
        return len(self.log.read_text().splitlines())

    def test_show_still_works_for_an_unaffected_basket(self):
        self.corrupt()
        code, out, err = self.run_cli("show", self.clean)
        self.assertEqual(code, 0, err)
        self.assertEqual(out["slug"], self.clean)

    def test_list_still_names_every_basket(self):
        self.corrupt()
        code, out, err = self.run_cli("list")
        self.assertEqual(code, 0, err)
        self.assertEqual(sorted(b["slug"] for b in out["baskets"]),
                         sorted([self.clean, self.other]))

    def test_history_and_export_still_work(self):
        self.corrupt()
        code, out, err = self.run_cli("history", self.clean)
        self.assertEqual(code, 0, err)
        self.assertTrue(out["events"])
        code, out, err = self.run_cli("export")
        self.assertEqual(code, 0, err)
        self.assertEqual(sorted(out["exported"]),
                         sorted([self.clean, self.other]))

    def test_verify_reports_the_line_number_and_exits_three(self):
        number = self.corrupt()
        code, out, err = self.run_cli("verify")
        self.assertEqual(code, 3)
        self.assertEqual([c["line"] for c in out["corrupt_lines"]], [number])
        # Every basket that replayed cleanly is still named in the report.
        self.assertEqual(sorted(out["baskets"]),
                         sorted([self.clean, self.other]))

    def test_verify_reports_every_corrupt_line(self):
        first = self.corrupt()
        second = self.corrupt()
        code, out, _ = self.run_cli("verify")
        self.assertEqual(code, 3)
        self.assertEqual([c["line"] for c in out["corrupt_lines"]],
                         [first, second])

    def test_a_write_still_works_after_a_corrupt_line(self):
        self.corrupt()
        code, _, err = self.run_cli("set-weight", self.clean,
                                    "--symbol", "NVDA", "--weight", "100")
        self.assertEqual(code, 0, err)

    def test_show_exits_zero_and_warns_of_the_skipped_line(self):
        number = self.corrupt()
        code, out, err = self.run_cli("show", self.clean)
        self.assertEqual(code, 0, err)
        self.assertIn("warnings", out)
        self.assertTrue(any(str(number) in w for w in out["warnings"]))

    def test_list_exits_zero_and_warns_of_the_skipped_line(self):
        number = self.corrupt()
        code, out, err = self.run_cli("list")
        self.assertEqual(code, 0, err)
        self.assertIn("warnings", out)
        self.assertTrue(any(str(number) in w for w in out["warnings"]))

    def test_history_exits_zero_and_warns_of_the_skipped_line(self):
        number = self.corrupt()
        code, out, err = self.run_cli("history", self.clean)
        self.assertEqual(code, 0, err)
        self.assertIn("warnings", out)
        self.assertTrue(any(str(number) in w for w in out["warnings"]))

    def test_export_exits_zero_and_warns_of_the_skipped_line(self):
        number = self.corrupt()
        code, out, err = self.run_cli("export")
        self.assertEqual(code, 0, err)
        self.assertIn("warnings", out)
        self.assertTrue(any(str(number) in w for w in out["warnings"]))

    def test_show_reports_no_warnings_key_on_an_intact_log(self):
        code, out, err = self.run_cli("show", self.clean)
        self.assertEqual(code, 0, err)
        self.assertNotIn("warnings", out)

    def test_list_reports_no_warnings_key_on_an_intact_log(self):
        code, out, err = self.run_cli("list")
        self.assertEqual(code, 0, err)
        self.assertNotIn("warnings", out)

    def test_history_reports_no_warnings_key_on_an_intact_log(self):
        code, out, err = self.run_cli("history", self.clean)
        self.assertEqual(code, 0, err)
        self.assertNotIn("warnings", out)

    def test_export_reports_no_warnings_key_on_an_intact_log(self):
        code, out, err = self.run_cli("export")
        self.assertEqual(code, 0, err)
        self.assertNotIn("warnings", out)

    def test_a_basket_whose_events_all_parsed_is_still_readable(self):
        self.corrupt()
        code, out, err = self.run_cli("show", self.clean)
        self.assertEqual(code, 0, err)
        self.assertEqual(out["slug"], self.clean)

    def test_verify_still_exits_three_on_a_corrupt_line(self):
        number = self.corrupt()
        code, out, err = self.run_cli("verify")
        self.assertEqual(code, 3)
        self.assertEqual([c["line"] for c in out["corrupt_lines"]], [number])

    def test_list_table_format_reports_the_warning(self):
        number = self.corrupt()
        proc = subprocess.run(
            [sys.executable, str(CLI), "--data-dir", str(self.data_dir),
             "list", "--format", "table"],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(str(number), proc.stdout)


class TestRealizedPnlIsProtected(CliTestCase):
    """A sold-out holding has 0 shares but may carry real money."""

    def setUp(self):
        CliTestCase.setUp(self)
        self.slug = self.make_basket(name="Pair", symbols="NVDA:50,MSFT:50")
        # Buy 100 at 10, sell 100 at 18: no shares left, 800.00 realized.
        batch = orders_response(
            order(order_id="b1", side="buy", quantity="100",
                  average_price="10.00",
                  created_at="2026-01-01T15:00:00Z",
                  last_transaction_at="2026-01-01T15:00:00Z"),
            order(order_id="s1", side="sell", quantity="100",
                  average_price="18.00",
                  created_at="2026-01-02T15:00:00Z",
                  last_transaction_at="2026-01-02T15:00:00Z"))
        code, _, err = self.run_cli(
            "record-fills", self.slug, "--orders-json", batch,
            "--order-ids", "b1,s1", "--account", "123456789")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.realized(), 800.00)

    def realized(self):
        return self.run_cli("show", self.slug)[1]["totals"]["realized_pnl"]

    def test_remove_holding_refuses_a_sold_out_holding_with_realized_pnl(self):
        code, _, err = self.run_cli("remove-holding", self.slug,
                                    "--symbol", "NVDA")
        self.assertEqual(code, 1)
        self.assertIn("HOLDING_HAS_REALIZED_PNL", err)
        self.assertIn("800.00", err)
        self.assertIn("--force", err)
        # The refusal changed nothing.
        self.assertEqual(self.realized(), 800.00)

    def test_force_removes_it_and_warns_about_the_loss(self):
        code, out, err = self.run_cli("remove-holding", self.slug,
                                      "--symbol", "NVDA", "--force")
        self.assertEqual(code, 0, err)
        self.assertTrue(any("800.00" in w for w in out["warnings"]))
        self.assertEqual(self.realized(), 0.00)

    def test_delete_refuses_a_basket_that_carries_realized_pnl(self):
        code, _, err = self.run_cli("delete", self.slug)
        self.assertEqual(code, 1)
        self.assertIn("BASKET_HAS_REALIZED_PNL", err)
        self.assertIn("--force", err)
        self.assertEqual(self.run_cli("list")[1]["baskets"][0]["slug"],
                         self.slug)

    def test_delete_force_removes_it_and_warns(self):
        code, out, err = self.run_cli("delete", self.slug, "--force")
        self.assertEqual(code, 0, err)
        self.assertTrue(any("800.00" in w for w in out["warnings"]))
        self.assertEqual(self.run_cli("list")[1]["baskets"], [])

    def test_a_holding_with_no_history_still_removes_freely(self):
        code, _, err = self.run_cli("remove-holding", self.slug,
                                    "--symbol", "MSFT")
        self.assertEqual(code, 0, err)


class TestSlugReuse(CliTestCase):
    """A deleted basket must not burn its order ids forever."""

    def test_delete_then_recreate_can_record_the_same_order(self):
        slug = self.make_basket(name="Solo", symbols="NVDA:100")
        response = orders_response(order(order_id="o1"))
        code, _, err = self.run_cli("record-fills", slug, "--orders-json",
                                    response, "--order-ids", "o1",
                                    "--account", "123456789")
        self.assertEqual(code, 0, err)
        code, _, err = self.run_cli("delete", slug, "--force")
        self.assertEqual(code, 0, err)

        again = self.make_basket(name="Solo", symbols="NVDA:100")
        self.assertEqual(again, slug)
        code, out, err = self.run_cli("record-fills", again, "--orders-json",
                                      response, "--order-ids", "o1",
                                      "--account", "123456789")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["recorded"], ["o1"])
        self.assertEqual(out["already_recorded"], [])
        # The shares reach the new basket instead of vanishing.
        shown = self.run_cli("show", again)[1]
        position = [h for h in shown["holdings"]
                    if h["symbol"] == "NVDA"][0]["position"]
        self.assertIsNotNone(position)
        self.assertEqual(position["shares"], 10.0)

    def test_a_deleted_basket_frees_its_order_for_a_different_basket(self):
        first = self.make_basket(name="First", symbols="NVDA:100")
        response = orders_response(order(order_id="o1"))
        self.run_cli("record-fills", first, "--orders-json", response,
                     "--order-ids", "o1", "--account", "123456789")
        self.run_cli("delete", first, "--force")

        second = self.make_basket(name="Second", symbols="NVDA:100")
        code, out, err = self.run_cli("record-fills", second, "--orders-json",
                                      response, "--order-ids", "o1",
                                      "--account", "123456789")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["recorded"], ["o1"])

    def test_a_live_cross_basket_duplicate_is_still_skipped(self):
        # The guarantee that matters: while both baskets are live, one order
        # can fund only one of them.
        first = self.make_basket(name="First", symbols="NVDA:100")
        second = self.make_basket(name="Second", symbols="NVDA:100")
        response = orders_response(order(order_id="o1"))
        code, _, err = self.run_cli("record-fills", first, "--orders-json",
                                    response, "--order-ids", "o1",
                                    "--account", "123456789")
        self.assertEqual(code, 0, err)
        code, _, err = self.run_cli("record-fills", second, "--orders-json",
                                    response, "--order-ids", "o1",
                                    "--account", "123456789")
        self.assertEqual(code, 1)
        self.assertIn("ORDER_IN_OTHER_BASKET", err)

    def test_history_keeps_both_lifetimes(self):
        slug = self.make_basket(name="Solo", symbols="NVDA:100")
        self.run_cli("delete", slug)
        self.make_basket(name="Solo", symbols="NVDA:100")
        events = self.run_cli("history", slug)[1]["events"]
        created = [e for e in events if e["type"] == "basket_created"]
        self.assertEqual(len(created), 2)
        self.assertEqual(len([e for e in events
                              if e["type"] == "basket_deleted"]), 1)


class TestVerifyWarnsWithoutLocking(CliTestCase):
    """These run in-process, because the flag is what is being patched."""

    def setUp(self):
        CliTestCase.setUp(self)
        self.slug = self.make_basket(name="Solo", symbols="NVDA:100")
        self.saved = basket_events.LOCKING_AVAILABLE

    def tearDown(self):
        basket_events.LOCKING_AVAILABLE = self.saved
        CliTestCase.tearDown(self)

    def verify(self):
        args = basket.build_parser().parse_args(
            ["verify", "--data-dir", str(self.data_dir)])
        return basket.cmd_verify(args, basket.Store(self.data_dir))

    def test_verify_warns_when_locking_is_unavailable(self):
        basket_events.LOCKING_AVAILABLE = False
        payload = self.verify()
        self.assertFalse(payload["locking_available"])
        self.assertTrue(any("no file locking" in w for w in payload["warnings"]),
                        payload["warnings"])

    def test_verify_does_not_warn_when_locking_works(self):
        payload = self.verify()
        self.assertTrue(payload["locking_available"])
        self.assertFalse(any("no file locking" in w
                             for w in payload["warnings"]))

    def test_the_tool_still_writes_and_reads_without_locking(self):
        basket_events.LOCKING_AVAILABLE = False
        args = basket.build_parser().parse_args(
            ["add-holding", self.slug, "--symbol", "MSFT", "--weight", "40",
             "--data-dir", str(self.data_dir)])
        basket.cmd_add_holding(args, basket.Store(self.data_dir))
        # Read it back through the subprocess path, which uses the real flag.
        shown = self.run_cli("show", self.slug)[1]
        self.assertEqual(sorted(h["symbol"] for h in shown["holdings"]),
                         ["MSFT", "NVDA"])


class TestPriceParsing(CliTestCase):

    def test_a_non_numeric_price_is_a_json_error_not_a_traceback(self):
        slug = self.make_basket(name="Solo", symbols="NVDA:100")
        code, _, err = self.run_cli("show", slug, "--prices", '{"NVDA":"abc"}')
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", err)
        envelope = json.loads(err)
        self.assertEqual(envelope["code"], "BAD_PRICE")
        self.assertEqual(envelope["detail"]["symbol"], "NVDA")

    def test_a_null_price_is_a_json_error(self):
        slug = self.make_basket(name="Solo", symbols="NVDA:100")
        code, _, err = self.run_cli("plan-buy", slug, "--amount", "100",
                                    "--prices", '{"NVDA":null}')
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(err)["code"], "BAD_PRICE")

    def test_a_numeric_string_price_still_works(self):
        slug = self.make_basket(name="Solo", symbols="NVDA:100")
        code, out, err = self.run_cli("plan-buy", slug, "--amount", "100",
                                      "--prices", '{"NVDA":"50.5"}')
        self.assertEqual(code, 0, err)
        self.assertEqual(out["orders"][0]["price"], 50.5)


class TestPlanning(CliTestCase):

    def setUp(self):
        CliTestCase.setUp(self)
        self.slug = self.make_basket(name="Pair", symbols="NVDA:60,MSFT:40")

    def test_plan_buy_allocates_the_full_amount(self):
        code, out, err = self.run_cli("plan-buy", self.slug, "--amount", "100")
        self.assertEqual(code, 0, err)
        amounts = dict((r["symbol"], r["amount"]) for r in out["orders"])
        self.assertEqual(amounts, {"NVDA": 60.0, "MSFT": 40.0})
        self.assertEqual(round(sum(amounts.values()), 2), 100.0)

    def test_plan_buy_allocates_every_cent_of_an_awkward_amount(self):
        # 33/33/34 over 10 cents: rounding each row on its own gives three
        # cents apiece and allocates 9. The largest-remainder pass must put
        # the tenth cent on the 34 percent holding.
        slug = self.make_basket(name="Thirds",
                                symbols="AAA:33,BBB:33,CCC:34")
        code, out, err = self.run_cli("plan-buy", slug, "--amount", "0.10")
        self.assertEqual(code, 0, err)
        amounts = dict((r["symbol"], r["amount"]) for r in out["orders"])
        self.assertEqual(amounts, {"AAA": 0.03, "BBB": 0.03, "CCC": 0.04})
        self.assertEqual(sum(amounts.values()), 0.10)

    def test_plan_buy_allocates_every_cent_across_seven_holdings(self):
        # Seven holdings at 15/15/14/14/14/14/14 over $10.01.
        slug = self.make_basket(
            name="Seven",
            symbols="AAA:1,BBB:1,CCC:1,DDD:1,EEE:1,FFF:1,GGG:1")
        code, out, err = self.run_cli("plan-buy", slug, "--amount", "10.01")
        self.assertEqual(code, 0, err)
        cents = sum(int(round(r["amount"] * 100)) for r in out["orders"])
        self.assertEqual(cents, 1001)

    def test_plan_buy_never_drifts_across_a_range_of_amounts(self):
        slug = self.make_basket(name="Trio", symbols="AAA:33,BBB:33,CCC:34")
        for amount in ("0.01", "0.07", "0.10", "1.00", "3.33", "99.99", "100"):
            code, out, err = self.run_cli("plan-buy", slug, "--amount", amount)
            self.assertEqual(code, 0, err)
            cents = sum(int(round(r["amount"] * 100)) for r in out["orders"])
            self.assertEqual(cents, int(round(float(amount) * 100)), amount)

    def test_plan_buy_returns_shares_with_prices(self):
        code, out, err = self.run_cli("plan-buy", self.slug, "--amount", "100",
                                      "--prices", '{"NVDA": 200, "MSFT": 100}')
        self.assertEqual(code, 0, err)
        shares = dict((r["symbol"], r["shares"]) for r in out["orders"])
        self.assertAlmostEqual(shares["NVDA"], 0.3)
        self.assertAlmostEqual(shares["MSFT"], 0.4)

    def test_plan_buy_on_an_empty_basket_exits_one(self):
        self.run_cli("remove-holding", self.slug, "--symbol", "NVDA")
        self.run_cli("remove-holding", self.slug, "--symbol", "MSFT")
        code, _, err = self.run_cli("plan-buy", self.slug, "--amount", "100")
        self.assertEqual(code, 1)
        self.assertIn("EMPTY_BASKET", err)

    def test_plan_sell_all_returns_every_share(self):
        response = orders_response(
            order(order_id="b1", symbol="NVDA", quantity="10", average_price="50.00"))
        self.run_cli("record-fills", self.slug, "--orders-json", response,
                     "--order-ids", "b1", "--account", "123456789")
        code, out, err = self.run_cli("plan-sell", self.slug, "--all")
        self.assertEqual(code, 0, err)
        shares = dict((r["symbol"], r["shares"]) for r in out["orders"])
        self.assertEqual(shares["NVDA"], 10.0)

    def test_plan_sell_all_with_prices_returns_proceeds(self):
        response = orders_response(
            order(order_id="b1", symbol="NVDA", quantity="10", average_price="50.00"))
        self.run_cli("record-fills", self.slug, "--orders-json", response,
                     "--order-ids", "b1", "--account", "123456789")
        code, out, err = self.run_cli("plan-sell", self.slug, "--all",
                                      "--prices", '{"NVDA": 70}')
        self.assertEqual(code, 0, err)
        self.assertEqual(out["estimated_proceeds"], 700.0)

    def test_plan_sell_amount_keeps_the_current_weights(self):
        # NVDA 10 @ 50 = 500, MSFT 10 @ 25 = 250. Total 750.
        response = orders_response(
            order(order_id="b1", symbol="NVDA", quantity="10", average_price="50.00"),
            order(order_id="b2", symbol="MSFT", quantity="10", average_price="25.00"))
        self.run_cli("record-fills", self.slug, "--orders-json", response,
                     "--order-ids", "b1,b2", "--account", "123456789")
        code, out, err = self.run_cli("plan-sell", self.slug, "--amount", "75",
                                      "--prices", '{"NVDA": 50, "MSFT": 25}')
        self.assertEqual(code, 0, err)
        shares = dict((r["symbol"], r["shares"]) for r in out["orders"])
        # 10 percent of each holding's value.
        self.assertAlmostEqual(shares["NVDA"], 1.0)
        self.assertAlmostEqual(shares["MSFT"], 1.0)

    def test_plan_sell_above_the_basket_value_is_refused(self):
        response = orders_response(
            order(order_id="b1", symbol="NVDA", quantity="10", average_price="50.00"))
        self.run_cli("record-fills", self.slug, "--orders-json", response,
                     "--order-ids", "b1", "--account", "123456789")
        code, _, err = self.run_cli("plan-sell", self.slug, "--amount", "5000",
                                    "--prices", '{"NVDA": 50}')
        self.assertEqual(code, 1)
        self.assertIn("AMOUNT_ABOVE_VALUE", err)

    def test_plan_sell_amount_needs_prices(self):
        code, _, err = self.run_cli("plan-sell", self.slug, "--amount", "10")
        self.assertEqual(code, 1)
        self.assertIn("PRICES_REQUIRED", err)


def positions_response(*pairs):
    rows = [{"symbol": s, "quantity": str(q), "average_buy_price": "50.00"}
            for s, q in pairs]
    return json.dumps({"data": {"positions": rows}})


class TestVerifyAndBackup(CliTestCase):

    def setUp(self):
        CliTestCase.setUp(self)
        self.slug = self.make_basket(name="Pair", symbols="NVDA:60,MSFT:40")
        response = orders_response(
            order(order_id="b1", symbol="NVDA", quantity="10", average_price="50.00"))
        self.run_cli("record-fills", self.slug, "--orders-json", response,
                     "--order-ids", "b1", "--account", "123456789")

    def test_claims_equal_position_is_correct(self):
        code, out, err = self.run_cli(
            "verify", "--positions", positions_response(("NVDA", 10)))
        self.assertEqual(code, 0, err)
        row = [r for r in out["positions"] if r["symbol"] == "NVDA"][0]
        self.assertEqual(row["state"], "match")

    def test_claims_below_position_is_normal(self):
        code, out, err = self.run_cli(
            "verify", "--positions", positions_response(("NVDA", 25)))
        self.assertEqual(code, 0, err)
        row = [r for r in out["positions"] if r["symbol"] == "NVDA"][0]
        self.assertEqual(row["state"], "outside_shares")
        # A claim below the account position must not add a *position*
        # warning. setUp's own second write (record-fills) leaves the backup
        # marker one event stale, which independently and correctly produces
        # a backup-staleness warning (see test_verify_warns_when_the_backup_
        # is_stale) - that is unrelated to this check and is not asserted
        # away here.
        self.assertFalse(any("Baskets claim" in w for w in out["warnings"]))

    def test_claims_above_position_is_reported(self):
        code, out, err = self.run_cli(
            "verify", "--positions", positions_response(("NVDA", 4)))
        self.assertEqual(code, 0, err)
        row = [r for r in out["positions"] if r["symbol"] == "NVDA"][0]
        self.assertEqual(row["state"], "over_claimed")
        self.assertTrue(out["warnings"])

    def test_verify_without_positions_skips_the_third_check(self):
        code, out, err = self.run_cli("verify")
        self.assertEqual(code, 0, err)
        self.assertIsNone(out["positions"])

    def test_verify_reports_an_ignored_duplicate(self):
        log = self.data_dir / "events.log.jsonl"
        lines = log.read_text().splitlines()
        trade = [l for l in lines if '"type":"buy"' in l][0]
        with log.open("a") as handle:
            handle.write(trade + "\n")
        code, out, err = self.run_cli("verify")
        self.assertEqual(code, 0, err)
        self.assertEqual(len(out["ignored_events"]), 1)
        self.assertEqual(out["ignored_events"][0]["order_id"], "b1")

    def test_a_corrupt_line_exits_three_and_names_the_line(self):
        log = self.data_dir / "events.log.jsonl"
        line_number = len(log.read_text().splitlines()) + 1
        with log.open("a") as handle:
            handle.write("{broken\n")
        code, out, err = self.run_cli("verify")
        # The integrity signal stays. verify now still prints its whole
        # report, so the user learns which line to repair and which baskets
        # replayed cleanly.
        self.assertEqual(code, 3)
        self.assertEqual(out["code"], "CORRUPT_LOG_LINE")
        self.assertEqual([c["line"] for c in out["corrupt_lines"]], [line_number])
        self.assertTrue(any("not valid JSON" in w for w in out["warnings"]))

    def test_backup_writes_a_copy_and_a_marker(self):
        code, out, err = self.run_cli("backup")
        self.assertEqual(code, 0, err)
        self.assertTrue(Path(out["path"]).exists())
        marker = json.loads((self.data_dir / "backup.marker").read_text())
        self.assertGreater(marker["events"], 0)

    def test_verify_warns_when_no_backup_exists(self):
        # setUp already wrote events, and the first write with no marker runs
        # a backup by itself. Remove the marker to reach the no-backup path.
        marker = self.data_dir / "backup.marker"
        if marker.exists():
            marker.unlink()
        out = self.run_cli("verify")[1]
        self.assertTrue(any("No backup marker" in w for w in out["warnings"]))

    def test_verify_warns_when_the_backup_is_stale(self):
        self.run_cli("backup")
        self.run_cli("set-weight", self.slug, "--symbol", "NVDA", "--weight", "70")
        out = self.run_cli("verify")[1]
        self.assertTrue(any("last backup held" in w for w in out["warnings"]))

    def test_verify_reports_a_clamped_oversell(self):
        # A hand-edited log can hold a sale larger than the basket ever held.
        log = self.data_dir / "events.log.jsonl"
        buy_line = [l for l in log.read_text().splitlines() if '"type":"buy"' in l][0]
        bad = buy_line.replace('"type":"buy"', '"type":"sell"')
        bad = bad.replace('"order_id":"b1"', '"order_id":"bad"')
        bad = bad.replace('"shares":10.0', '"shares":999.0')
        with log.open("a") as handle:
            handle.write(bad + "\n")
        out = self.run_cli("verify")[1]
        self.assertTrue(out["clamped_sells"])
        self.assertTrue(any("dropped" in w for w in out["warnings"]))

    def test_verify_accepts_a_slug(self):
        code, out, err = self.run_cli("verify", self.slug)
        self.assertEqual(code, 0, err)
        self.assertEqual(out["baskets"], [self.slug])

    def test_backup_honours_the_to_option(self):
        target = self.data_dir / "elsewhere"
        code, out, err = self.run_cli("backup", "--to", str(target))
        self.assertEqual(code, 0, err)
        self.assertTrue(Path(out["path"]).parent == target)

    def test_verify_stops_warning_after_a_backup(self):
        self.run_cli("backup")
        out = self.run_cli("verify")[1]
        self.assertFalse(any("backup" in w.lower() for w in out["warnings"]))

    def test_the_tool_backs_up_by_itself(self):
        # The setUp already wrote several events. Drive the count past the
        # threshold and confirm a backup appeared without an explicit call.
        for index in range(12):
            self.run_cli("set-weight", self.slug, "--symbol", "NVDA",
                         "--weight", str(50 + (index % 5)))
        backups = list((self.data_dir / "backups").glob("*.jsonl"))
        self.assertTrue(backups)


class TestVerifyPositionsAcrossBaskets(CliTestCase):
    """Finding 2: a slug filter must not hide a store-wide over-claim.

    alpha claims 6 NVDA, beta claims 5, and the account holds 8. The whole
    store is over-claimed (11 > 8). `verify alpha --positions` used to
    compare alpha's own 6 against the account's 8 and call that
    `outside_shares` - a "nothing to do here" result - when the truth is
    that alpha's basket-mate is why the account looks short.
    """

    def setUp(self):
        CliTestCase.setUp(self)
        self.alpha = self.make_basket(name="Alpha", symbols="NVDA:100")
        self.run_cli(
            "record-fills", self.alpha, "--orders-json",
            orders_response(order(order_id="a1", symbol="NVDA", quantity="6",
                                  average_price="50.00")),
            "--order-ids", "a1", "--account", "123456789")
        self.beta = self.make_basket(name="Beta", symbols="NVDA:100")
        self.run_cli(
            "record-fills", self.beta, "--orders-json",
            orders_response(order(order_id="b1", symbol="NVDA", quantity="5",
                                  average_price="55.00")),
            "--order-ids", "b1", "--account", "123456789")

    def test_unfiltered_verify_reports_the_combined_over_claim(self):
        code, out, err = self.run_cli(
            "verify", "--positions", positions_response(("NVDA", 8)))
        self.assertEqual(code, 0, err)
        row = [r for r in out["positions"] if r["symbol"] == "NVDA"][0]
        self.assertEqual(row["claimed"], 11.0)
        self.assertEqual(row["state"], "over_claimed")

    def test_a_slug_filtered_verify_still_reports_the_combined_over_claim(self):
        code, out, err = self.run_cli(
            "verify", self.alpha, "--positions", positions_response(("NVDA", 8)))
        self.assertEqual(code, 0, err)
        row = [r for r in out["positions"] if r["symbol"] == "NVDA"][0]
        # The claimed total is the WHOLE store's (6 + 5), not just alpha's
        # own 6 - that is the bug this test guards against.
        self.assertEqual(row["claimed"], 11.0)
        self.assertEqual(row["state"], "over_claimed")

    def test_the_warning_names_the_other_basket(self):
        code, out, err = self.run_cli(
            "verify", self.alpha, "--positions", positions_response(("NVDA", 8)))
        self.assertEqual(code, 0, err)
        self.assertTrue(any(self.beta in w for w in out["warnings"]))

    def test_a_slug_filtered_verify_only_lists_that_baskets_symbols(self):
        code, out, err = self.run_cli(
            "verify", self.alpha, "--positions", positions_response(("NVDA", 8)))
        self.assertEqual(code, 0, err)
        self.assertEqual([r["symbol"] for r in out["positions"]], ["NVDA"])


if __name__ == "__main__":
    unittest.main()
