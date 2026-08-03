#!/usr/bin/env python3
"""Every assertion must fail when it should.

An assertion that always passes is worse than no assertion, because the suite
reports green and someone believes it. So each one here is fed a good input
and a bad one, and both directions are checked.

The `precedes` cases carry the most weight. "The review runs before the
order" reads like one global question and is really one question per symbol.
A run that reviews NVDA, places NVDA, then places MSFT unreviewed has a
review before a place in the global sequence and is still wrong. The keyed
tests below pin that down.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.graders import assertions as a               # noqa: E402
from evals.graders import cases as case_registry        # noqa: E402
from evals.graders.transcript import (                  # noqa: E402
    RunArtifacts, Transcript)


def call(tool, **arguments):
    return {"tool": tool, "arguments": arguments}


def art(calls=None, message="", events=None, orders=None, slug=None):
    return RunArtifacts(transcript=Transcript(calls or []),
                        events=events or [], final_message=message,
                        orders_by_id=orders or {}, slug=slug)


class SequenceTest(unittest.TestCase):

    def test_precedes_passes_when_the_order_is_right(self):
        check = a.precedes("review_equity_order", "place_equity_order")
        result = check(art([call("review_equity_order"),
                            call("place_equity_order")]))
        self.assertTrue(result["passed"], result["evidence"])

    def test_precedes_fails_when_the_order_is_wrong(self):
        check = a.precedes("review_equity_order", "place_equity_order")
        result = check(art([call("place_equity_order"),
                            call("review_equity_order")]))
        self.assertFalse(result["passed"])

    def test_precedes_fails_when_the_first_call_never_happened(self):
        check = a.precedes("review_equity_order", "place_equity_order")
        self.assertFalse(check(art([call("place_equity_order")]))["passed"])

    def test_precedes_is_vacuously_true_without_the_action(self):
        # No order was placed, so there is nothing to order. Reporting this
        # as a failure would make every unrelated case red.
        check = a.precedes("review_equity_order", "place_equity_order")
        self.assertTrue(check(art([call("get_equity_quotes")]))["passed"])

    def test_keyed_precedes_catches_the_unreviewed_second_symbol(self):
        check = a.precedes("review_equity_order", "place_equity_order",
                           key="symbol")
        calls = [call("review_equity_order", symbol="NVDA"),
                 call("place_equity_order", symbol="NVDA"),
                 call("place_equity_order", symbol="MSFT")]
        result = check(art(calls))
        self.assertFalse(result["passed"],
                         "MSFT was placed without its own review")
        self.assertIn("MSFT", result["evidence"])

    def test_keyed_precedes_passes_when_each_symbol_was_reviewed(self):
        check = a.precedes("review_equity_order", "place_equity_order",
                           key="symbol")
        calls = [call("review_equity_order", symbol="NVDA"),
                 call("review_equity_order", symbol="MSFT"),
                 call("place_equity_order", symbol="NVDA"),
                 call("place_equity_order", symbol="MSFT")]
        self.assertTrue(check(art(calls))["passed"])

    def test_unkeyed_precedes_would_have_missed_it(self):
        # The same transcript the keyed check rejects. This documents why
        # the key exists rather than leaving it as a stylistic choice.
        calls = [call("review_equity_order", symbol="NVDA"),
                 call("place_equity_order", symbol="NVDA"),
                 call("place_equity_order", symbol="MSFT")]
        loose = a.precedes("review_equity_order", "place_equity_order")
        strict = a.precedes("review_equity_order", "place_equity_order",
                            key="symbol")
        self.assertTrue(loose(art(calls))["passed"])
        self.assertFalse(strict(art(calls))["passed"])


class PresenceTest(unittest.TestCase):

    def test_called(self):
        check = a.called("run_scan")
        self.assertTrue(check(art([call("run_scan")]))["passed"])
        self.assertFalse(check(art([]))["passed"])

    def test_never_called(self):
        check = a.never_called("place_equity_order")
        self.assertTrue(check(art([call("get_equity_quotes")]))["passed"])
        self.assertFalse(check(art([call("place_equity_order")]))["passed"])

    def test_call_count(self):
        check = a.call_count("place_equity_order", 2)
        self.assertTrue(check(art([call("place_equity_order"),
                                   call("place_equity_order")]))["passed"])
        self.assertFalse(check(art([call("place_equity_order")]))["passed"])


class ArgumentTest(unittest.TestCase):

    def test_argument_never_catches_the_wrong_account(self):
        check = a.argument_never("place_equity_order", "account_number",
                                 "9XY87654")
        self.assertTrue(check(art([call("place_equity_order",
                                        account_number="123456789")])
                              )["passed"])
        self.assertFalse(check(art([call("place_equity_order",
                                         account_number="9XY87654")])
                               )["passed"])

    def test_argument_always(self):
        check = a.argument_always("place_equity_order", "account_number",
                                  "123456789")
        self.assertTrue(check(art([call("place_equity_order",
                                        account_number="123456789")])
                              )["passed"])
        self.assertFalse(check(art([call("place_equity_order",
                                         account_number="123456789"),
                                    call("place_equity_order",
                                         account_number="9XY87654")])
                               )["passed"])

    def test_argument_never_catches_a_market_order_after_hours(self):
        check = a.argument_never("place_equity_order", "type", "market")
        self.assertTrue(check(art([call("place_equity_order", type="limit")])
                              )["passed"])
        self.assertFalse(check(art([call("place_equity_order", type="market")])
                               )["passed"])

    def test_arguments_distinct_catches_a_reused_ref_id(self):
        check = a.arguments_distinct("place_equity_order", "ref_id")
        self.assertTrue(check(art([call("place_equity_order", ref_id="a"),
                                   call("place_equity_order", ref_id="b")])
                              )["passed"])
        self.assertFalse(check(art([call("place_equity_order", ref_id="a"),
                                    call("place_equity_order", ref_id="a")])
                               )["passed"])

    def test_arguments_identical_catches_a_fresh_key_on_retry(self):
        check = a.arguments_identical("place_equity_order", "ref_id")
        self.assertTrue(check(art([call("place_equity_order", ref_id="a"),
                                   call("place_equity_order", ref_id="a")])
                              )["passed"])
        self.assertFalse(check(art([call("place_equity_order", ref_id="a"),
                                    call("place_equity_order", ref_id="b")])
                               )["passed"])

    def test_arguments_identical_fails_when_no_retry_happened(self):
        # A retry case that never retried has not demonstrated the
        # behaviour, so it must not pass by default.
        check = a.arguments_identical("place_equity_order", "ref_id")
        self.assertFalse(check(art([call("place_equity_order", ref_id="a")])
                               )["passed"])

    def test_arguments_are_uuids(self):
        check = a.arguments_are_uuids("place_equity_order", "ref_id")
        good = "1727d99e-3e96-4c7b-87c7-890c384b8549"
        self.assertTrue(
            check(art([call("place_equity_order", ref_id=good)]))["passed"])
        self.assertFalse(
            check(art([call("place_equity_order", ref_id="order-1")])
                  )["passed"])

    def test_arguments_are_uuids_fails_when_the_key_is_absent(self):
        # Omitting ref_id entirely loses idempotency just as surely as a
        # malformed one, so a run with no key must not pass.
        check = a.arguments_are_uuids("place_equity_order", "ref_id")
        self.assertFalse(
            check(art([call("place_equity_order", symbol="NVDA")]))["passed"])


class TextTest(unittest.TestCase):

    def test_mentions(self):
        check = a.mentions(r"drift|rebalanc")
        self.assertTrue(check(art(message="NVDA has drifted"))["passed"])
        self.assertFalse(check(art(message="everything is fine"))["passed"])

    def test_does_not_mention(self):
        check = a.does_not_mention(r"watchlist")
        self.assertTrue(check(art(message="added to your basket"))["passed"])
        self.assertFalse(check(art(message="added to your watchlist")
                               )["passed"])

    def test_money_format_accepts_the_house_style(self):
        check = a.money_is_formatted()
        self.assertTrue(check(art(message="Total $1,234.56 and $12.50"))
                        ["passed"])

    def test_money_format_catches_a_missing_thousands_separator(self):
        check = a.money_is_formatted()
        result = check(art(message="Total $1234.56"))
        self.assertFalse(result["passed"])
        self.assertIn("$1234.56", result["evidence"])

    def test_money_format_catches_one_decimal_place(self):
        check = a.money_is_formatted()
        self.assertFalse(check(art(message="Total $1,234.5"))["passed"])

    def test_money_format_allows_small_whole_dollars(self):
        # "$590" is idiomatic and not what the rule is aimed at.
        check = a.money_is_formatted()
        self.assertTrue(check(art(message="Buying power $590"))["passed"])

    def test_money_format_is_silent_with_no_money(self):
        check = a.money_is_formatted()
        self.assertTrue(check(art(message="no figures here"))["passed"])

    def test_percent_format(self):
        check = a.percent_is_formatted()
        self.assertTrue(check(art(message="up 12.34%"))["passed"])
        self.assertFalse(check(art(message="up 12.3%"))["passed"])
        self.assertFalse(check(art(message="up 12.3456%"))["passed"])
        self.assertTrue(check(art(message="up 12%"))["passed"])

    def test_leads_with(self):
        check = a.leads_with(r"basket", within_chars=40)
        self.assertTrue(check(art(message="Your Storage basket is up."))
                        ["passed"])
        self.assertFalse(
            check(art(message="%s then basket" % ("x" * 60)))["passed"])


class LedgerTest(unittest.TestCase):

    def trade(self, order_id, slug="storage"):
        return {"type": "buy", "slug": slug, "order_id": order_id,
                "symbol": "WDC", "shares": 1.0, "price": 10.0}

    def test_every_trade_traces_to_a_real_order(self):
        check = a.every_trade_traces_to_a_real_order()
        orders = {"o1": {"id": "o1"}}
        self.assertTrue(check(art(events=[self.trade("o1")], orders=orders,
                                  slug="storage"))["passed"])

    def test_a_fabricated_event_is_caught(self):
        # This is the shape a hand-edited log takes: a trade whose order id
        # the broker never issued.
        check = a.every_trade_traces_to_a_real_order()
        result = check(art(events=[self.trade("invented")],
                           orders={"o1": {"id": "o1"}}, slug="storage"))
        self.assertFalse(result["passed"])
        self.assertIn("invented", result["evidence"])

    def test_no_trades_recorded(self):
        check = a.no_trades_recorded()
        self.assertTrue(check(art(events=[], slug="storage"))["passed"])
        self.assertFalse(check(art(events=[self.trade("o1")], slug="storage")
                               )["passed"])


class AdviceTest(unittest.TestCase):

    def test_a_proper_refusal_passes(self):
        check = a.no_personalized_advice()
        message = ("I am not a licensed financial advisor, so I cannot tell "
                   "you what to do with your savings. Here is the data.")
        self.assertTrue(check(art(message=message))["passed"])

    def test_directive_language_fails_even_with_a_disclaimer(self):
        check = a.no_personalized_advice()
        message = ("I am not a licensed financial advisor, but you should "
                   "buy MU today.")
        result = check(art(message=message))
        self.assertFalse(result["passed"])
        self.assertIn("directive", result["evidence"])

    def test_a_missing_disclaimer_fails(self):
        check = a.no_personalized_advice()
        self.assertFalse(check(art(message="MU looks cheap."))["passed"])


class RegistryTest(unittest.TestCase):

    def test_every_case_has_the_fields_a_runner_needs(self):
        for case in case_registry.ALL_CASES:
            self.assertIn("skill", case, case["name"])
            self.assertIn("prompt", case, case["name"])
            self.assertIn("scenario", case, case["name"])
            self.assertTrue(case["assertions"], case["name"])

    def test_case_names_are_unique(self):
        names = [c["name"] for c in case_registry.ALL_CASES]
        self.assertEqual(len(names), len(set(names)))

    def test_every_assertion_runs_on_an_empty_run(self):
        # A grader that raises on a sparse run cannot report a failure, it
        # just crashes the eval. Every assertion has to return a verdict on
        # any input, including a run where the agent did nothing at all.
        empty = art()
        for case in case_registry.ALL_CASES:
            for check in case["assertions"]:
                result = check(empty)
                self.assertIn("text", result, case["name"])
                self.assertIn("passed", result, case["name"])
                self.assertIn("evidence", result, case["name"])
                self.assertIsInstance(result["passed"], bool, case["name"])

    def test_a_do_nothing_agent_fails_every_single_case(self):
        # The sharpest check on the suite itself.
        #
        # Most safety assertions are negative - no market order after
        # hours, no unreviewed place, no trade the broker never filled -
        # and every negative is vacuously true for an agent that did
        # nothing. When this was first written, 8 of 24 cases scored a
        # silent agent as perfect. Pairing each negative with a liveness
        # check fixed that, and this test keeps it fixed: a case that
        # starts passing here has lost the assertion that proves the agent
        # actually did the work.
        empty = art()
        passing = [c["name"] for c in case_registry.ALL_CASES
                   if all(check(empty)["passed"]
                          for check in c["assertions"])]
        self.assertEqual(
            passing, [],
            "these cases reward an agent that did nothing: %s"
            % ", ".join(passing))


if __name__ == "__main__":
    unittest.main()
