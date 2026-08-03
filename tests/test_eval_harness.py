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

    def test_a_market_order_is_refused_outside_regular_hours(self):
        self.broker.regular_hours = False
        with self.assertRaises(broker_state.BrokerError):
            self.server.call_tool("place_equity_order", {
                "account_number": ACCOUNT, "symbol": "WDC", "side": "buy",
                "type": "market", "dollar_amount": "20.00"})

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
