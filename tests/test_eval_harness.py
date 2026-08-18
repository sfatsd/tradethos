#!/usr/bin/env python3
"""Tests for the evaluation harness itself.

A grader that never fails is worse than no grader, because it reports green
while the defect ships. So the harness needs its own tests, and the important
ones are the negative cases: the grader must fail when the agent misbehaves.

Three agent behaviours are simulated against the same filled orders:

  well_behaved   forwards the raw get_equity_orders response
  trimming       strips the timestamps, the way the agent did on 2026-08-03
  pre_fix        writes the events the old code wrote for a trimmed response

`trimming` and `pre_fix` are different failures and the grader must tell them
apart. With the required-field check in place, a trimmed response records
nothing, so the failure is completeness. If someone later reverts that check,
the trimmed response records again, with wall-clock timestamps - and only the
timestamp assertion catches it. `pre_fix` exists to keep that assertion
honest, because it is the one that would have caught the original defect.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "skills" / "basket-manager" / "scripts" / "basket.py"
sys.path.insert(0, str(ROOT))

from evals.fake_mcp import state as broker_state          # noqa: E402
from evals.fake_mcp.server import Server                  # noqa: E402
from evals.graders.check_record_fills import (            # noqa: E402
    grade, index_orders, load_events)

ACCOUNT = broker_state.AGENTIC_ACCOUNT
BASKET_BUY = [("WDC", "20.00"), ("STX", "20.00"), ("MU", "20.00")]


class HarnessTestCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = Path(self.tmp.name)
        self.broker = broker_state.Broker()
        self.server = Server(self.broker)
        self.slug = self.run_cli(
            "create", "Storage", "--symbols", "WDC:34,STX:33,MU:33",
            "--account", ACCOUNT)[1]["slug"]

    def run_cli(self, *args):
        proc = subprocess.run(
            [sys.executable, str(CLI), "--data-dir", str(self.data_dir)]
            + list(args), capture_output=True, text=True)
        payload = None
        if proc.stdout.strip():
            try:
                payload = json.loads(proc.stdout)
            except ValueError:
                payload = proc.stdout
        return proc.returncode, payload, proc.stderr

    def place_basket_buy(self):
        """Place the orders and return their ids, newest last."""
        ids = []
        for symbol, amount in BASKET_BUY:
            result = self.server.call_tool("place_equity_order", {
                "account_number": ACCOUNT, "symbol": symbol, "side": "buy",
                "type": "market", "dollar_amount": amount})
            ids.append(result["data"]["order"]["id"])
        return ids

    def orders_response(self):
        return self.server.call_tool("get_equity_orders",
                                     {"account_number": ACCOUNT})

    def grade_now(self, order_ids):
        return grade(load_events(self.data_dir / "events.log.jsonl"),
                     index_orders(self.orders_response()),
                     order_ids, self.slug)

    @staticmethod
    def failures(results):
        return [r["text"] for r in results if not r["passed"]]

    # --- the three behaviours -------------------------------------------

    def test_well_behaved_agent_passes_every_assertion(self):
        order_ids = self.place_basket_buy()
        code, _, err = self.run_cli(
            "record-fills", self.slug, "--order-ids", ",".join(order_ids),
            "--account", ACCOUNT,
            "--orders-json", json.dumps(self.orders_response()))
        self.assertEqual(code, 0, err)

        results = self.grade_now(order_ids)
        self.assertEqual(self.failures(results), [],
                         "a correct agent must pass every assertion")

    def test_trimming_agent_is_caught_on_completeness(self):
        order_ids = self.place_basket_buy()
        trimmed = self.orders_response()
        for order in trimmed["data"]["orders"]:
            order.pop("last_transaction_at", None)
            order.pop("created_at", None)
            for execution in order.get("executions") or []:
                execution.pop("timestamp", None)

        code, _, err = self.run_cli(
            "record-fills", self.slug, "--order-ids", ",".join(order_ids),
            "--account", ACCOUNT, "--orders-json", json.dumps(trimmed))
        self.assertEqual(code, 1)
        self.assertIn("MISSING_REQUIRED_FIELDS", err)

        results = self.grade_now(order_ids)
        self.assertIn("Every placed order reached the basket ledger",
                      self.failures(results))

    def test_pre_fix_behaviour_is_caught_on_the_timestamp(self):
        # Reproduce what the old code wrote: the right shares, the right
        # price, and utc_now() in place of the fill time. Everything a
        # report would show is correct, so only the timestamp assertion
        # can find it. This is the assertion that would have caught the
        # 2026-08-03 defect, and this test keeps it working.
        order_ids = self.place_basket_buy()
        by_id = index_orders(self.orders_response())
        wall_clock = "2026-08-03T15:41:35.111Z"

        log = self.data_dir / "events.log.jsonl"
        with open(log, "a") as handle:
            for order_id in order_ids:
                order = by_id[order_id]
                handle.write(json.dumps({
                    "v": 1, "ts": wall_clock, "type": "buy",
                    "slug": self.slug, "symbol": order["symbol"],
                    "shares": float(order["cumulative_quantity"]),
                    "price": float(order["average_price"]),
                    "amount": (float(order["cumulative_quantity"])
                               * float(order["average_price"])),
                    "order_id": order_id}) + "\n")

        results = self.grade_now(order_ids)
        failed = self.failures(results)
        self.assertEqual(
            failed,
            ["Each event is stamped with the real fill time, "
             "not the time the command ran"],
            "the timestamp assertion must be the only one that fails")

    # --- the fake broker itself -----------------------------------------

    def test_the_fill_price_is_the_ask_not_the_last_trade(self):
        # An agent that records the quote instead of the execution produces
        # a plausible wrong number. The fake has to separate the two or the
        # price assertion can never fail.
        order = self.server.call_tool("place_equity_order", {
            "account_number": ACCOUNT, "symbol": "WDC", "side": "buy",
            "type": "market", "dollar_amount": "20.00"})["data"]["order"]
        quotes = self.broker.get_equity_quotes(["WDC"])
        quote = quotes["data"]["results"][0]["quote"]
        self.assertEqual(float(order["average_price"]),
                         float(quote["ask_price"]))
        self.assertNotEqual(float(order["average_price"]),
                            float(quote["last_trade_price"]))

    def test_a_repeated_ref_id_does_not_buy_twice(self):
        first = self.server.call_tool("place_equity_order", {
            "account_number": ACCOUNT, "symbol": "WDC", "side": "buy",
            "type": "market", "dollar_amount": "20.00",
            "ref_id": "same-key"})["data"]["order"]
        second = self.server.call_tool("place_equity_order", {
            "account_number": ACCOUNT, "symbol": "WDC", "side": "buy",
            "type": "market", "dollar_amount": "20.00",
            "ref_id": "same-key"})["data"]["order"]
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.broker.order_sequence), 1)

    def test_orders_come_back_newest_first(self):
        # The real API returns them this way, and an agent that forwards
        # ids in response order records history backwards. The fake has to
        # reproduce the hazard or no eval can test for it.
        order_ids = self.place_basket_buy()
        returned = [o["id"] for o
                    in self.orders_response()["data"]["orders"]]
        self.assertEqual(returned, list(reversed(order_ids)))

    def test_the_non_agentic_account_is_refused(self):
        with self.assertRaises(broker_state.BrokerError):
            self.server.call_tool("place_equity_order", {
                "account_number": broker_state.NON_AGENTIC_ACCOUNT,
                "symbol": "WDC", "side": "buy", "type": "market",
                "dollar_amount": "20.00"})

    def test_a_market_order_after_hours_queues_rather_than_failing(self):
        # This test used to assert a refusal, which was a rule the real
        # broker does not have. The documented behaviour is that a market
        # order placed after hours as regular_hours is accepted and queued
        # for the next open. The queue is the hazard: a user who asked to
        # buy "right now" gets no error, and a fill tomorrow at a price
        # nobody quoted them.
        broker = broker_state.Broker(regular_hours=False)
        order = broker.place_equity_order(
            ACCOUNT, "WDC", "buy", "market",
            dollar_amount="20.00")["data"]["order"]
        self.assertEqual(order["state"], "queued")
        self.assertEqual(float(order["cumulative_quantity"]), 0.0)
        self.assertIsNone(order["average_price"])
        self.assertEqual(order["executions"], [])
        self.assertNotIn("WDC", [s for s, h in broker.positions.items()
                                 if h["quantity"] > 0.05])

    def test_a_market_order_tagged_to_another_session_is_refused(self):
        with self.assertRaises(broker_state.BrokerError):
            self.server.call_tool("place_equity_order", {
                "account_number": ACCOUNT, "symbol": "WDC", "side": "buy",
                "type": "market", "dollar_amount": "20.00",
                "market_hours": "extended_hours"})

    def test_a_limit_order_fills_in_extended_hours(self):
        broker = broker_state.Broker(regular_hours=False)
        order = broker.place_equity_order(
            ACCOUNT, "WDC", "buy", "limit", quantity="1",
            market_hours="extended_hours")["data"]["order"]
        self.assertEqual(order["state"], "filled")

    def test_an_undeclared_tool_name_is_refused(self):
        # Dispatch used to read the name straight off the broker, so any
        # method was reachable. `__init__` would reset the broker mid-run
        # and `record_call` would let the agent write its own transcript.
        # A harness that exists to report what the agent did cannot answer
        # to names it never published.
        for name in ("__init__", "record_call", "_tick"):
            with self.assertRaises(broker_state.BrokerError):
                self.server.call_tool(name, {})

    def test_an_undeclared_name_is_not_written_to_the_transcript(self):
        try:
            self.server.call_tool("record_call", {"tool": "x",
                                                  "arguments": {}})
        except broker_state.BrokerError:
            pass
        self.assertEqual(self.broker.calls, [])

    def test_the_transcript_records_call_order(self):
        self.server.call_tool("review_equity_order", {
            "account_number": ACCOUNT, "symbol": "WDC", "side": "buy",
            "type": "market", "dollar_amount": "20.00"})
        self.server.call_tool("place_equity_order", {
            "account_number": ACCOUNT, "symbol": "WDC", "side": "buy",
            "type": "market", "dollar_amount": "20.00"})
        tools = [c["tool"] for c in self.broker.calls]
        self.assertLess(tools.index("review_equity_order"),
                        tools.index("place_equity_order"))


if __name__ == "__main__":
    unittest.main()

class BrokerCorrectnessTest(unittest.TestCase):
    """Behaviours a fake has to get right to be worth trusting."""

    def setUp(self):
        self.broker = broker_state.Broker()
        self.server = Server(self.broker)

    def test_a_sell_reduces_the_holding_and_returns_cash(self):
        # It used to add shares and take cash whatever the side, so a sell
        # increased the position. No case sells today, but the grader
        # accepts sell events, so this was a trap set for the next case.
        before = self.broker.positions["WDC"]["quantity"]
        cash = self.broker.cash
        self.broker.place_equity_order(ACCOUNT, "WDC", "sell", "market",
                                       quantity="0.02")
        self.assertAlmostEqual(self.broker.positions["WDC"]["quantity"],
                               before - 0.02, places=6)
        self.assertGreater(self.broker.cash, cash)

    def test_a_sell_cannot_exceed_the_holding(self):
        with self.assertRaises(broker_state.BrokerError):
            self.broker.place_equity_order(ACCOUNT, "WDC", "sell", "market",
                                           quantity="99")

    def test_a_sell_leaves_the_average_cost_alone(self):
        before = self.broker.positions["WDC"]["average_buy_price"]
        self.broker.place_equity_order(ACCOUNT, "WDC", "sell", "market",
                                       quantity="0.01")
        self.assertEqual(self.broker.positions["WDC"]["average_buy_price"],
                         before)

    def test_buying_power_is_enforced(self):
        broker = broker_state.Broker(cash=10.0)
        with self.assertRaises(broker_state.BrokerError):
            broker.place_equity_order(ACCOUNT, "WDC", "buy", "market",
                                      dollar_amount="5000")
        self.assertEqual(broker.cash, 10.0)

    def test_the_notional_reconciles_at_the_fill_price(self):
        # Every real fill on 2026-08-03 came back within a hundredth of a
        # cent of its dollar amount. Sizing the shares off the quote left
        # the fake out by the spread, so an eval checking the arithmetic
        # would have been measuring the fake rather than the agent.
        order = self.broker.place_equity_order(
            ACCOUNT, "NVDA", "buy", "market",
            dollar_amount="50.00")["data"]["order"]
        notional = (float(order["cumulative_quantity"])
                    * float(order["average_price"]))
        self.assertAlmostEqual(notional, 50.00, places=2)

    def test_the_fill_price_is_still_not_the_quote(self):
        order = self.broker.place_equity_order(
            ACCOUNT, "NVDA", "buy", "market",
            dollar_amount="50.00")["data"]["order"]
        self.assertNotEqual(float(order["average_price"]),
                            self.broker.quotes["NVDA"]["last"])

    def test_a_rounded_up_fraction_does_not_widen_the_field(self):
        stamp = broker_state._stamp(100.9999, 3)
        self.assertRegex(stamp, r"\.\d{3}Z$")
        self.assertEqual(stamp, "2026-08-03T00:01:41.000Z")

    def test_strip_order_timestamps_removes_every_fill_time(self):
        broker = broker_state.Broker(strip_order_timestamps=True)
        broker.place_equity_order(ACCOUNT, "WDC", "buy", "market",
                                  dollar_amount="20.00")
        order = broker.get_equity_orders(ACCOUNT)["data"]["orders"][0]
        self.assertNotIn("created_at", order)
        self.assertNotIn("last_transaction_at", order)
        self.assertNotIn("timestamp", order["executions"][0])

    def test_run_scan_answers_the_question_it_was_asked(self):
        losers = self.broker.run_scan(preset="daily_losers")["data"]["results"]
        gainers = self.broker.run_scan(
            preset="daily_gainers")["data"]["results"]
        self.assertNotEqual(losers, gainers)
        self.assertLess(float(losers[0]["percent_change"]),
                        float(gainers[0]["percent_change"]))

    def test_run_scan_rejects_an_unknown_scan_id(self):
        with self.assertRaises(broker_state.BrokerError):
            self.broker.run_scan(scan_id="never-created")


class ServerRobustnessTest(unittest.TestCase):
    """A bad argument is an agent mistake, not an infrastructure failure."""

    def setUp(self):
        self.server = Server(broker_state.Broker())

    def probe(self, name, arguments):
        return self.server.handle({"jsonrpc": "2.0", "id": 1,
                                   "method": "tools/call",
                                   "params": {"name": name,
                                              "arguments": arguments}})

    def test_an_unexpected_argument_returns_a_tool_error(self):
        response = self.probe("get_accounts", {"account_number": "1"})
        self.assertIn("result", response)

    def test_a_missing_argument_returns_a_tool_error(self):
        response = self.probe("get_equity_quotes", {})
        self.assertIn("result", response)
        self.assertNotIn("error", response)

    def test_the_loop_survives_a_bad_call(self):
        # The failure that matters: an unhandled TypeError killed serve()
        # mid-run, and the eval then read as a harness failure rather than
        # an agent failure - the most expensive kind of wrong result,
        # because it discredits the suite instead of the agent.
        self.probe("get_equity_positions", {"account_number": "1", "x": 2})
        good = self.probe("get_accounts", {})
        self.assertIn("accounts", good["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
