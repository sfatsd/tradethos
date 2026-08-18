"""Build the canned responses a case serves.

The `Broker` still shapes these, but only here and only once, before any
agent runs. That keeps what it was always good at - payload shapes that match
the real API, including the details a tidier fake would smooth away - and
drops what it was bad at, which was deciding anything while an agent watched.

The distinction is worth stating plainly, because it is the whole design.

A *simulation* answers a request by working out what should happen. It can be
wrong about the rules, and when it is, a correct agent fails the eval and
someone goes looking for a bug in a skill that does not have one. Every
defect this harness produced was of that kind.

A *fixture* answers a request by handing back something written earlier. It
can be unrealistic, which is a real cost, but it cannot be wrong about a rule
it never applies. Nothing here reads the request's arguments.

Consistency is arranged rather than computed. `place_equity_order` returns a
fixed order, and `get_equity_orders` returns that same order, so the sequence
place -> read back -> record still holds together and the recorded event has a
known `last_transaction_at` to be checked against. That is the sequence that
wrote six wrong timestamps into the real ledger, and it stays testable.
"""

import copy
import json

from .fake_mcp import state as broker_state

# Fixed, so an assertion can name them. A generated id would make the
# expected value unknowable until the run had already happened.
ORDER_IDS = ["11111111-1111-4111-8111-111111111111",
             "22222222-2222-4222-8222-222222222222",
             "33333333-3333-4333-8333-333333333333"]
FILL_TIMES = ["2026-08-03T15:32:24.660Z",
              "2026-08-03T15:32:27.860Z",
              "2026-08-03T15:32:31.060Z"]
CREATED_TIMES = ["2026-08-03T15:32:24.474980Z",
                 "2026-08-03T15:32:27.674980Z",
                 "2026-08-03T15:32:30.874980Z"]


def _order(index, symbol, dollar_amount="50.00", side="buy"):
    """One filled order, in the shape the real API returns."""
    broker = broker_state.Broker()
    quote = broker.quotes[symbol]
    fill = quote["ask"] if side == "buy" else quote["bid"]
    shares = round(float(dollar_amount) / fill, 6)
    return {
        "id": ORDER_IDS[index], "instrument_id": "inst-%s" % symbol,
        "symbol": symbol, "side": side, "type": "market", "state": "filled",
        "quantity": "%.6f" % shares, "cumulative_quantity": "%.6f" % shares,
        "price": "%.6f" % quote["last"], "stop_price": None,
        "average_price": "%.6f" % fill, "fees": "0.000000",
        "dollar_based_amount": {"amount": "%.6f" % float(dollar_amount),
                                "currency_code": "USD"},
        "time_in_force": "gfd", "market_hours": "regular_hours",
        "trigger": "immediate", "placed_agent": "agentic",
        # Six fractional digits on one, three on the other. The real API is
        # inconsistent this way and code that parses one format and not the
        # other passes against a tidier fixture.
        "created_at": CREATED_TIMES[index],
        "last_transaction_at": FILL_TIMES[index],
        "executions": [{"id": "exec-%d" % index, "price": "%.6f" % fill,
                        "quantity": "%.6f" % shares,
                        "timestamp": FILL_TIMES[index],
                        "fees": "0.000000"}],
    }


def _strip_times(order):
    trimmed = dict((k, v) for k, v in order.items()
                   if k not in ("created_at", "last_transaction_at"))
    trimmed["executions"] = [
        dict((k, v) for k, v in e.items() if k != "timestamp")
        for e in order.get("executions") or []]
    return trimmed


def build(case, symbols=("WDC", "MU")):
    """Return the canned responses for one case."""
    scenario = case.get("scenario") or {}
    broker = broker_state.Broker(
        quotes=scenario.get("quotes"),
        positions=scenario.get("positions"),
        cash=scenario.get("cash", 1000.0))
    halted = set(scenario.get("halted") or ())

    known = sorted(broker.quotes)
    orders = [_order(i, s) for i, s in enumerate(symbols[:len(ORDER_IDS)])]
    listed = list(reversed(orders))          # newest first, as the API does
    if scenario.get("strip_order_timestamps"):
        listed = [_strip_times(o) for o in listed]

    responses = {
        "get_accounts": broker.get_accounts(),
        "get_portfolio": broker.get_portfolio(None),
        "get_equity_positions": broker.get_equity_positions(None),
        "get_equity_quotes": broker.get_equity_quotes(known),
        "get_equity_tradability": {"data": {"results": [
            {"symbol": s, "name": s, "simple_name": s,
             "state": "inactive" if s in halted else "active",
             "country": "US", "tradeable": s not in halted,
             "fractional_tradability": ("untradable" if s in halted
                                        else "tradable"),
             "extended_hours_fractional_tradability": False,
             "all_day_tradability": ("untradable" if s in halted
                                     else "tradable"),
             "short_selling_tradability": "tradable",
             "account_type_tradabilities": [
                 {"account_type": "individual",
                  "account_type_tradability": ("untradable" if s in halted
                                               else "tradable")}]}
            for s in sorted(set(known) | halted)]}},
        "review_equity_order": {"data": {
            "symbol": symbols[0], "side": "buy", "type": "market",
            "order_checks": {},
            "quote_data": broker.get_equity_quotes(
                [symbols[0]])["data"]["results"][0]["quote"],
            "market_data_disclosure": "Bid/Ask/Last as quoted."}},
        "place_equity_order": [{"data": {"order": o}} for o in orders],
        "get_equity_orders": {"data": {"orders": listed}},
        "get_equity_fundamentals": broker.get_equity_fundamentals(known),
        "get_equity_historicals": broker.get_equity_historicals(known[:2]),
        "get_equity_technical_indicators":
            broker.get_equity_technical_indicators(known[:2]),
        "get_scanner_filter_specs": broker.get_scanner_filter_specs(),
        "create_scan": {"data": {"scan": {"id": "scan-1", "name": "probe"}}},
        "run_scan": broker.run_scan(preset="daily_losers"),
    }

    if scenario.get("fail_next_place"):
        # A scripted sequence, not a decision. The case wants one transport
        # failure and then a success, so the list says exactly that and
        # nothing at run time works out when to fail.
        failures = [{"error": "upstream timeout", "transport": True}]
        responses["place_equity_order"] = (
            failures * int(scenario["fail_next_place"])
            + [{"data": {"order": o}} for o in orders])

    return responses


def expected_orders(case, symbols=("WDC", "MU")):
    """The orders a case's responses will hand back, keyed by id."""
    return dict((o["id"], o) for o in
                json.loads(json.dumps(
                    [_order(i, s) for i, s
                     in enumerate(symbols[:len(ORDER_IDS)])])))


def write(case, path, symbols=("WDC", "MU")):
    payload = build(case, symbols)
    with open(path, "w") as handle:
        handle.write(json.dumps(payload, indent=2) + "\n")
    return copy.deepcopy(payload)
