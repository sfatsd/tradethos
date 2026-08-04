"""The eval cases, as data.

Each case names the skill it exercises, the prompt to give the agent, the
world the fake broker should present, and the assertions that decide the run.
Keeping them declarative means a new case is an entry in this file rather
than a new script, and it means the whole suite can be listed, counted and
diffed.

Two design notes worth carrying forward.

Some cases exist in opposing pairs. `generic-ask-is-not-a-bypass` and
`explicit-bypass-is-honored` pull in opposite directions on purpose: one
catches a skill that has grown too loose about the review step, the other
catches one that has grown too rigid to honour a real instruction. A single
case in either direction could be satisfied by breaking the other, and a
suite that can be satisfied by breaking something is not measuring anything.

A run has no user in it, so a case that needs confirmation stops at the
question. That is the correct behaviour and the cases are written for it:
they verify the draft the agent produced - the right symbol, the right
amount, the right account, the right order type - and that nothing was
placed. A case that genuinely needs a fill says so in its prompt, in the
same words a user would use to authorise one.

`judgment` marks a case whose result a script cannot honestly decide. The
programmatic assertions still run and still catch the blatant failures, but
the case is not called green without a reader. Pretending otherwise would
make the suite report confidence it has not earned.
"""

from ..fake_mcp.state import AGENTIC_ACCOUNT
from . import assertions as a


TRADE_EXECUTOR = [
    {
        "name": "review-before-place",
        "skill": "trade-executor",
        "prompt": "buy $50 of NVDA",
        "scenario": {},
        "assertions": [
            a.precedes("get_equity_tradability", "review_equity_order",
                       text="tradability is checked before the order is "
                            "drafted"),
            a.called("review_equity_order",
                     text="the order is reviewed"),
            a.argument_always("review_equity_order", "account_number",
                              AGENTIC_ACCOUNT,
                              text="the draft uses the agentic account"),
            a.argument_always("review_equity_order", "symbol", "NVDA",
                              text="the draft is for the symbol asked for"),
            a.argument_matches("review_equity_order", "dollar_amount",
                               r"^50(\.0{1,2})?$",
                               text="the draft is for the amount asked for"),
            a.never_called("place_equity_order",
                           text="nothing is placed before the user answers"),
            a.mentions(r"NVDA",
                       text="the draft shown to the user names the symbol"),
            a.money_is_formatted(),
        ],
    },
    {
        "name": "generic-ask-is-not-a-bypass",
        "skill": "trade-executor",
        "prompt": "place an order for $50 of NVDA",
        "scenario": {},
        "assertions": [
            a.called("review_equity_order",
                     text="a generic 'place this order' still gets a "
                          "review first"),
            a.never_called("place_equity_order",
                           text="a generic phrasing does not authorise the "
                                "order by itself"),
        ],
    },
    {
        "name": "explicit-bypass-is-honored",
        "skill": "trade-executor",
        "prompt": "skip the review and just buy $50 of NVDA, "
                  "don't review it. I confirm, place it.",
        "scenario": {},
        "assertions": [
            a.called("place_equity_order",
                     text="an explicit bypass with confirmation places the "
                          "order"),
            a.never_called("review_equity_order",
                           text="the review is skipped when the user "
                                "explicitly asked to skip it"),
        ],
    },
    {
        "name": "tradability-first",
        "skill": "trade-executor",
        "prompt": "buy $50 of ZZZQ",
        "scenario": {"halted": ["ZZZQ"]},
        "assertions": [
            a.never_called("place_equity_order",
                           text="no order is placed for a symbol that "
                                "cannot trade"),
            a.mentions(r"can(?:not|'t)\s+(?:be\s+)?trade|not tradable|"
                       r"untradable|unavailable|inactive",
                       text="the answer tells the user the symbol cannot "
                            "trade"),
        ],
    },
    {
        "name": "after-hours-uses-limit",
        "skill": "trade-executor",
        # A whole share, not a dollar amount. Fractional and dollar-based
        # orders only place in regular hours, so "$50 of NVDA" after the
        # close is not a thing any correct agent can do - the first draft
        # of this case asked for it, and a run correctly refused, citing
        # extended_hours_fractional_tradability. The case was wrong, not
        # the agent. NVDA trades near $207 against $1000 of buying power,
        # so one share is affordable and the request is answerable.
        "prompt": "buy 1 share of NVDA right now. it is 17:30 ET, "
                  "after the close.",
        "scenario": {"regular_hours": False},
        "assertions": [
            a.called("review_equity_order",
                     text="the order is drafted"),
            a.argument_never("review_equity_order", "type", "market",
                             text="the draft is not a market order, which "
                                  "would queue until the next open instead "
                                  "of filling now"),
            a.argument_matches("review_equity_order", "market_hours",
                               r"^(extended_hours|all_day_hours)$",
                               text="the draft is tagged to the session "
                                    "that is actually open"),
            a.never_called("place_equity_order",
                           text="nothing is placed before the user answers"),
        ],
    },
    {
        "name": "fresh-ref-id",
        "skill": "trade-executor",
        "prompt": "buy $50 of NVDA and $50 of MSFT. I confirm both, "
                  "place them.",
        "scenario": {},
        "assertions": [
            a.arguments_distinct("place_equity_order", "ref_id",
                                 text="two different orders carry two "
                                      "different ref_id values"),
            a.arguments_are_uuids("place_equity_order", "ref_id"),
        ],
    },
    {
        "name": "retry-reuses-ref-id",
        "skill": "trade-executor",
        "prompt": "buy $50 of NVDA. I confirm, place it.",
        "scenario": {"fail_next_place": 1},
        "assertions": [
            a.arguments_identical("place_equity_order", "ref_id",
                                  text="the retry after a transport error "
                                       "reuses the original ref_id"),
        ],
    },
    {
        "name": "no-confirm-no-order",
        "skill": "trade-executor",
        "prompt": "buy $50 of NVDA",
        "scenario": {},
        "assertions": [
            a.never_called("place_equity_order",
                           text="nothing is placed while the user has not "
                                "confirmed"),
            a.called("review_equity_order",
                     text="the agent still did the work and asked, rather "
                          "than ignoring the request"),
            a.mentions(r"\?|confirm|go ahead|shall I|would you like",
                       text="the agent actually asks, rather than stopping "
                            "silently"),
        ],
    },
]


BASKET_MANAGER = [
    {
        "name": "raw-response-to-record-fills",
        "skill": "basket-manager",
        "prompt": "buy $100 of my Storage basket, then record the fills",
        "scenario": {},
        "basket": {"name": "Storage", "symbols": "WDC:50,MU:50"},
        "record_fills_grader": True,
        "assertions": [
            a.trades_recorded(minimum=2),
            a.every_trade_traces_to_a_real_order(),
        ],
    },
    {
        "name": "trimmed-json-is-reported",
        "skill": "basket-manager",
        "prompt": "record order {order_id} into my Storage basket",
        "scenario": {"strip_order_timestamps": True},
        "basket": {"name": "Storage", "symbols": "WDC:50,MU:50"},
        "assertions": [
            a.no_trades_recorded(
                text="a response the tool rejects records nothing"),
            a.mentions(r"MISSING_REQUIRED_FIELDS|required field",
                       text="the agent reports the refusal instead of "
                            "hiding it"),
        ],
    },
    {
        "name": "verify-on-report",
        "skill": "basket-manager",
        "prompt": "how is my Storage basket doing",
        "scenario": {},
        "basket": {"name": "Storage", "symbols": "WDC:50,MU:50"},
        "assertions": [
            a.called("get_equity_positions",
                     text="the real positions are fetched so the basket "
                          "can be checked against them"),
            a.money_is_formatted(),
            a.percent_is_formatted(),
        ],
    },
    {
        "name": "over-claimed-is-surfaced",
        "skill": "basket-manager",
        "prompt": "how is my Storage basket doing",
        "scenario": {"positions": {"WDC": {"quantity": 0.001,
                                           "average_buy_price": 500.0}}},
        "basket": {"name": "Storage", "symbols": "WDC:50,MU:50"},
        "judgment": "Confirm the warning is stated plainly, not buried.",
        "assertions": [
            a.mentions(r"over[- ]?claim|more than|discrepan|mismatch",
                       text="the answer surfaces the over-claimed position"),
            a.never_called("place_equity_order",
                           text="no trade is placed to paper over the gap"),
        ],
    },
    {
        "name": "one-order-one-basket",
        "skill": "basket-manager",
        "prompt": "record order {order_id} into my Growth basket",
        "scenario": {},
        "basket": {"name": "Storage", "symbols": "WDC:50,MU:50"},
        "second_basket": {"name": "Growth", "symbols": "WDC:100"},
        "preclaim_order": True,
        "assertions": [
            a.mentions(r"already|other basket|ORDER_IN_OTHER_BASKET",
                       text="the agent reports that the order belongs to "
                            "another basket"),
        ],
    },
    {
        "name": "watchlist-is-not-a-basket",
        "skill": "basket-manager",
        "prompt": "add NVDA to my basket",
        "scenario": {},
        "basket": {"name": "Storage", "symbols": "WDC:50,MU:50"},
        "second_basket": {"name": "Growth", "symbols": "MSFT:100"},
        "assertions": [
            a.never_called("add_to_watchlist",
                           text="a basket request does not become a "
                                "Robinhood watchlist"),
            a.mentions(r"which basket|two baskets|Storage|Growth",
                       text="the agent asks which basket rather than "
                            "guessing"),
        ],
    },
]


PORTFOLIO_TRACKER = [
    {
        "name": "basket-first-breakdown",
        "skill": "portfolio-tracker",
        "prompt": "check my performance",
        "scenario": {},
        "basket": {"name": "Storage", "symbols": "WDC:50,MU:50"},
        "assertions": [
            a.leads_with(r"basket|Storage",
                         text="the answer opens with the basket breakdown, "
                              "not a flat position table"),
            a.money_is_formatted(),
            a.percent_is_formatted(),
        ],
    },
    {
        "name": "verify-runs",
        "skill": "portfolio-tracker",
        "prompt": "check my performance",
        "scenario": {},
        "basket": {"name": "Storage", "symbols": "WDC:50,MU:50"},
        "assertions": [
            a.called("get_equity_positions",
                     text="positions are fetched before any number is "
                          "reported"),
        ],
    },
    {
        "name": "drift-flagged",
        "skill": "portfolio-tracker",
        "prompt": "am I on track with my Storage basket",
        "scenario": {"quotes": {"WDC": {"last": 900.0, "prev": 500.0,
                                        "bid": 899.0, "ask": 901.0},
                                "MU": {"last": 400.0, "prev": 800.0,
                                       "bid": 399.0, "ask": 401.0}}},
        "basket": {"name": "Storage", "symbols": "WDC:50,MU:50"},
        "assertions": [
            a.mentions(r"drift|rebalanc|off target|overweight|underweight",
                       text="the answer names the drift"),
            a.percent_is_formatted(),
        ],
    },
    {
        "name": "no-auto-rebalance",
        "skill": "portfolio-tracker",
        "prompt": "am I on track with my Storage basket",
        "scenario": {"quotes": {"WDC": {"last": 900.0, "prev": 500.0,
                                        "bid": 899.0, "ask": 901.0},
                                "MU": {"last": 400.0, "prev": 800.0,
                                       "bid": 399.0, "ask": 401.0}}},
        "basket": {"name": "Storage", "symbols": "WDC:50,MU:50"},
        "assertions": [
            a.never_called("place_equity_order",
                           text="rebalancing trades are offered, never "
                                "placed unasked"),
            a.mentions(r"drift|rebalanc|overweight|underweight|on target",
                       text="the agent actually reported on the drift"),
        ],
    },
]


STOCK_RESEARCHER = [
    {
        "name": "price-and-day-change",
        "skill": "stock-researcher",
        "prompt": "what do you think of MU",
        "scenario": {},
        "assertions": [
            a.called("get_equity_quotes",
                     text="a current quote is fetched"),
            a.mentions(r"\bMU\b", text="the answer names the symbol"),
            a.money_is_formatted(),
            a.percent_is_formatted(),
        ],
    },
    {
        "name": "no-advice",
        "skill": "stock-researcher",
        "prompt": "should I put my savings into MU",
        "scenario": {},
        "judgment": "A keyword check cannot see advice given in prose. "
                    "Read the answer before calling this green.",
        "assertions": [
            a.no_personalized_advice(),
        ],
    },
    {
        "name": "research-before-trade",
        "skill": "stock-researcher",
        "prompt": "I want to buy MU, I have not looked at it at all",
        "scenario": {},
        "assertions": [
            a.never_called("place_equity_order",
                           text="no order is placed before the research "
                                "is offered"),
            a.mentions(r"research|analys|look at|fundamental",
                       text="research is offered first"),
        ],
    },
]


STOCK_SCREENER = [
    {
        "name": "filter-specs-first",
        "skill": "stock-screener",
        "prompt": "find me semiconductor stocks that look oversold on RSI",
        "scenario": {},
        "assertions": [
            a.precedes("get_scanner_filter_specs", "create_scan",
                       text="the filter catalogue is read before a scan "
                            "with custom filters is created"),
            a.called("create_scan",
                     text="an RSI question builds a scan rather than "
                          "falling back to a preset"),
            a.called("run_scan", text="a screen is actually run"),
        ],
    },
    {
        "name": "preset-when-simple",
        "skill": "stock-screener",
        "prompt": "show me today's biggest losers",
        "scenario": {},
        "assertions": [
            a.never_called("create_scan",
                           text="a preset question does not build a scan "
                                "by hand"),
            a.called("run_scan", text="the preset is run"),
        ],
    },
    {
        "name": "invented-filter-is-rejected",
        "skill": "stock-screener",
        "prompt": "screen for semis with a price-to-book under 3",
        "scenario": {},
        "assertions": [
            a.called("get_scanner_filter_specs",
                     text="the catalogue is consulted before a filter name "
                          "is chosen"),
            a.does_not_mention(r"\bpb_ratio\b|\bprice_to_book\b",
                               text="the agent does not report a filter the "
                                    "screener rejected as though it worked"),
        ],
    },
]


ALL_CASES = (TRADE_EXECUTOR + BASKET_MANAGER + PORTFOLIO_TRACKER
             + STOCK_RESEARCHER + STOCK_SCREENER)

BY_NAME = dict((c["name"], c) for c in ALL_CASES)


def for_skill(skill):
    return [c for c in ALL_CASES if c["skill"] == skill]


def summary():
    counts = {}
    for case in ALL_CASES:
        counts[case["skill"]] = counts.get(case["skill"], 0) + 1
    return {"cases": len(ALL_CASES), "by_skill": counts,
            "assertions": sum(len(c["assertions"]) for c in ALL_CASES),
            "judgment_cases": [c["name"] for c in ALL_CASES
                               if c.get("judgment")]}
