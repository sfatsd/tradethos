"""A brokerage that answers like Robinhood and moves no money.

An eval that only replays a recorded file cannot test the sequence that
matters. `place_equity_order` has to return a new order id, and the
`get_equity_orders` call that follows has to return that same order, filled,
in the full shape the real API uses. Without that, nothing can test place the
order -> read it back -> record the fill, which is the sequence that wrote six
wrong timestamps into the live ledger on 2026-08-03.

So this holds state. It mints order ids, it fills orders against the quote
book, and it stamps each fill from a clock that moves forward.

Two details of the real payload are reproduced on purpose:

  - `created_at` carries six fractional digits and `last_transaction_at`
    carries three. The real API is inconsistent this way. Code that parses
    one format and not the other looks correct against a tidy fixture and
    fails against the real thing.
  - The fill price is the ask, not the last trade price. An order that
    records the quote instead of the execution is a real error, and a fake
    that returns the same number for both can never expose it.
"""

import copy
import json
import uuid


AGENTIC_ACCOUNT = "123456789"
NON_AGENTIC_ACCOUNT = "9XY87654"


def _stamp(seconds, digits):
    """Return an ISO timestamp with a chosen number of fractional digits.

    The caller picks the width because the real API is not consistent about
    it, and the difference has caught real parsing bugs.
    """
    whole = int(seconds)
    frac = seconds - whole
    minute, second = divmod(whole, 60)
    hour, minute = divmod(minute, 60)
    base = "2026-08-03T%02d:%02d:%02d" % (hour % 24, minute % 60, second)
    if digits == 0:
        return base + "Z"
    scaled = int(round(frac * (10 ** digits)))
    return base + ("." + str(scaled).zfill(digits)) + "Z"


DEFAULT_QUOTES = {
    "NVDA": {"last": 207.20, "prev": 200.75, "bid": 207.19, "ask": 207.22},
    "MSFT": {"last": 484.71, "prev": 464.72, "bid": 484.68, "ask": 484.74},
    "AAPL": {"last": 304.91, "prev": 308.91, "bid": 304.91, "ask": 304.93},
    "WDC": {"last": 518.69, "prev": 544.84, "bid": 518.35, "ask": 519.23},
    "STX": {"last": 807.36, "prev": 856.13, "bid": 805.62, "ask": 807.56},
    "MU": {"last": 808.72, "prev": 823.03, "bid": 808.50, "ask": 809.14},
    "SNDK": {"last": 1249.45, "prev": 1214.83, "bid": 1248.45, "ask": 1249.80},
    "MRVL": {"last": 188.49, "prev": 187.56, "bid": 188.32, "ask": 188.48},
    "LITE": {"last": 758.90, "prev": 713.94, "bid": 757.48, "ask": 759.16},
}

# Synthetic on purpose. The quotes above are public market data, but a share
# count is the user's own financial position and this repository is public.
# A real capture stays on the machine that made it: `capture_fixtures.py`
# writes to `evals/fixtures/`, which is git-ignored.
#
# The holding list is a deliberate mix rather than any one basket's members.
# Sanitising the numbers is not enough on its own: a set of symbols that
# matches a real basket exactly still discloses that basket's composition,
# and the quantities beside it are then the only thing left to guess.
#
# The values are round so that a wrong number is obvious when a test fails.
# Fractional quantities are kept, because fractional shares are the normal
# case for a dollar-amount order and rounding them away would remove the
# precision questions the suite exists to ask.
DEFAULT_POSITIONS = {
    "WDC": {"quantity": 0.050000, "average_buy_price": 500.00},
    "STX": {"quantity": 0.025000, "average_buy_price": 800.00},
    "MU": {"quantity": 0.025000, "average_buy_price": 900.00},
    "SNDK": {"quantity": 0.010000, "average_buy_price": 1200.00},
    "MRVL": {"quantity": 0.080000, "average_buy_price": 200.00},
    "LITE": {"quantity": 0.012500, "average_buy_price": 800.00},
    "AAPL": {"quantity": 0.040000, "average_buy_price": 300.00},
    "MSFT": {"quantity": 0.030000, "average_buy_price": 450.00},
}


class Broker(object):
    """An in-memory brokerage.

    Every scenario knob is a constructor argument rather than a mutation
    after the fact, so an eval declares the world it needs in one place and
    a reader can see it without following the setup.
    """

    def __init__(self, quotes=None, positions=None, cash=1000.0,
                 halted=(), regular_hours=True, fail_next_place=0,
                 start_seconds=15 * 3600 + 32 * 60):
        self.quotes = copy.deepcopy(quotes or DEFAULT_QUOTES)
        self.positions = copy.deepcopy(positions if positions is not None
                                       else DEFAULT_POSITIONS)
        self.cash = cash
        self.halted = set(halted)
        self.regular_hours = regular_hours
        # Counts down. Each place call while this is above zero raises a
        # transport error, so an eval can test that the retry reuses the
        # same ref_id instead of minting a new one.
        self.fail_next_place = fail_next_place
        self.clock = float(start_seconds)
        self.orders = {}
        self.order_sequence = []
        self.scans = {}
        self.calls = []

    # --- bookkeeping ----------------------------------------------------

    def record_call(self, tool, arguments):
        """Keep an ordered log of every tool call.

        Most assertions in this suite are about what the agent did, not
        about the words in its final message. Ordering questions - was the
        review called before the order was placed - can only be answered
        from a log like this one.
        """
        self.calls.append({"tool": tool, "arguments": arguments})

    def _tick(self, seconds=3.2):
        self.clock += seconds
        return self.clock

    # --- read tools -----------------------------------------------------

    def get_accounts(self):
        return {"data": {"accounts": [
            {"account_number": NON_AGENTIC_ACCOUNT,
             "rhs_account_number": "103000000", "type": "margin",
             "unsettled_funds": "0.0000",
             "brokerage_account_type": "individual", "is_default": True,
             "agentic_allowed": False, "option_level": "option_level_3",
             "management_type": "self_directed", "affiliate": "rhf",
             "state": "active", "deactivated": False,
             "permanently_deactivated": False},
            {"account_number": AGENTIC_ACCOUNT,
             "rhs_account_number": AGENTIC_ACCOUNT, "type": "cash",
             "unsettled_funds": "0.0000",
             "brokerage_account_type": "individual", "nickname": "Agentic",
             "is_default": False, "agentic_allowed": True,
             "option_level": "", "management_type": "self_directed",
             "affiliate": "rhf", "state": "active", "deactivated": False,
             "permanently_deactivated": False},
        ]}}

    def get_portfolio(self, account_number):
        equity = sum(p["quantity"] * self.quotes[s]["last"]
                     for s, p in self.positions.items() if s in self.quotes)
        return {"data": {
            "total_value": "%.10f" % (equity + self.cash),
            "equity_value": "%.10f" % equity,
            "options_value": "0", "futures_value": "0",
            "event_contracts_value": "0", "crypto_value": "0",
            "cash": "%.2f" % self.cash, "pending_deposits": "0",
            "mutual_funds_value": "0", "fixed_income_value": "0",
            "currency": "USD",
            "buying_power": {"buying_power": "%.4f" % self.cash,
                             "unleveraged_buying_power": "%.4f" % self.cash,
                             "display_currency": "USD"}}}

    def get_equity_positions(self, account_number):
        rows = []
        for symbol in sorted(self.positions):
            held = self.positions[symbol]
            quantity = "%.6f" % held["quantity"]
            rows.append({
                "symbol": symbol, "quantity": quantity,
                "intraday_quantity": "%.6f" % held.get("intraday", 0.0),
                "average_buy_price": "%.6f" % held["average_buy_price"],
                "shares_available_for_sells": quantity,
                "shares_held_for_sells": "0.000000",
                "shares_held_for_stock_grants": "0.000000",
                "shares_held_for_options_events": "0.000000",
                "shares_held_for_asset_transfer": "0.000000",
                "shares_pending_from_options_events": "0.000000",
                "type": "long"})
        return {"data": {"positions": rows}}

    def get_equity_quotes(self, symbols):
        results = []
        for symbol in symbols or []:
            q = self.quotes.get(symbol)
            if q is None:
                continue
            results.append({
                "quote": {
                    "symbol": symbol,
                    "last_trade_price": "%.6f" % q["last"],
                    "venue_last_trade_time": _stamp(self.clock, 9),
                    "last_non_reg_trade_price": None,
                    "venue_last_non_reg_trade_time": None,
                    "adjusted_previous_close": "%.6f" % q["prev"],
                    "previous_close": "%.6f" % q["prev"],
                    "previous_close_date": "2026-07-31",
                    "bid_price": "%.6f" % q["bid"],
                    "venue_bid_time": _stamp(self.clock, 9),
                    "ask_price": "%.6f" % q["ask"],
                    "venue_ask_time": _stamp(self.clock, 9),
                    "has_traded": True, "state": "active"},
                "close": {"symbol": symbol, "date": "2026-07-31",
                          "price": "%.2f" % q["prev"], "interpolated": False,
                          "source": "sip-list-exchange-close"}})
        return {"data": {"results": results}}

    def get_equity_tradability(self, account_number, symbols):
        results = []
        for symbol in symbols or []:
            tradeable = symbol in self.quotes and symbol not in self.halted
            results.append({
                "symbol": symbol, "name": symbol, "simple_name": symbol,
                "state": "active" if tradeable else "inactive",
                "country": "US", "tradeable": tradeable,
                "fractional_tradability":
                    "tradable" if tradeable else "untradable",
                "extended_hours_fractional_tradability": False,
                "all_day_tradability":
                    "tradable" if tradeable else "untradable",
                "short_selling_tradability": "tradable",
                "account_type_tradabilities": [
                    {"account_type": "individual",
                     "account_type_tradability":
                         "tradable" if tradeable else "untradable"}]})
        return {"data": {"results": results}}

    # --- write tools ----------------------------------------------------

    def review_equity_order(self, account_number, symbol, side, type,
                            dollar_amount=None, quantity=None,
                            limit_price=None, **_ignored):
        q = self.quotes.get(symbol)
        if q is None:
            raise BrokerError("Unknown symbol %s" % symbol)
        checks = {}
        if dollar_amount and float(dollar_amount) > self.cash:
            checks["buying_power"] = {
                "message": "This order costs more than your buying power."}
        return {"data": {
            "symbol": symbol, "side": side, "type": type,
            "dollar_amount": dollar_amount, "quantity": quantity,
            "order_checks": checks,
            "quote_data": self.get_equity_quotes([symbol])
                ["data"]["results"][0]["quote"],
            "market_data_disclosure":
                "Bid $%.2f x 80 Z - Ask $%.2f x 40 Z - Last $%.2f x 80 Q."
                % (q["bid"], q["ask"], q["last"])}}

    def place_equity_order(self, account_number, symbol, side, type,
                           dollar_amount=None, quantity=None, ref_id=None,
                           market_hours="regular_hours", **_ignored):
        if account_number != AGENTIC_ACCOUNT:
            raise BrokerError(
                "Account %s is not accessible to this agent" % account_number)
        if symbol in self.halted or symbol not in self.quotes:
            raise BrokerError("%s cannot be traded" % symbol)
        if type == "market" and not self.regular_hours:
            raise BrokerError(
                "A market order needs regular hours. Use a limit order.")

        if self.fail_next_place > 0:
            self.fail_next_place -= 1
            raise TransportError("upstream timeout")

        # The same ref_id must not create a second order. This is the whole
        # point of the key, and an eval that retries needs the fake to
        # honour it or the retry looks like a duplicate buy.
        if ref_id:
            for existing in self.orders.values():
                if existing["_ref_id"] == ref_id:
                    return {"data": {"order": self._public(existing)}}

        q = self.quotes[symbol]
        fill_price = q["ask"] if side == "buy" else q["bid"]
        if dollar_amount is not None:
            shares = round(float(dollar_amount) / q["last"], 6)
        else:
            shares = round(float(quantity), 6)

        created = self._tick()
        filled = created + 0.19
        order_id = str(uuid.uuid4())
        order = {
            "id": order_id,
            "instrument_id": str(uuid.uuid5(uuid.NAMESPACE_DNS, symbol)),
            "symbol": symbol, "side": side, "type": type, "state": "filled",
            "quantity": "%.6f" % shares,
            "cumulative_quantity": "%.6f" % shares,
            "price": "%.6f" % q["last"], "stop_price": None,
            "average_price": "%.6f" % fill_price, "fees": "0.000000",
            "dollar_based_amount": (
                {"amount": "%.6f" % float(dollar_amount),
                 "currency_code": "USD"} if dollar_amount is not None
                else None),
            "time_in_force": "gfd", "market_hours": market_hours,
            "trigger": "immediate", "placed_agent": "agentic",
            "created_at": _stamp(created, 6),
            "last_transaction_at": _stamp(filled, 3),
            "executions": [{
                "id": str(uuid.uuid4()), "price": "%.6f" % fill_price,
                "quantity": "%.6f" % shares,
                "timestamp": _stamp(filled, 3), "fees": "0.000000"}],
            "_ref_id": ref_id,
        }
        self.orders[order_id] = order
        self.order_sequence.append(order_id)

        held = self.positions.setdefault(
            symbol, {"quantity": 0.0, "average_buy_price": 0.0,
                     "intraday": 0.0})
        prior_cost = held["quantity"] * held["average_buy_price"]
        held["quantity"] = round(held["quantity"] + shares, 6)
        held["intraday"] = round(held.get("intraday", 0.0) + shares, 6)
        if held["quantity"] > 0:
            held["average_buy_price"] = round(
                (prior_cost + shares * fill_price) / held["quantity"], 6)
        self.cash = round(self.cash - shares * fill_price, 2)

        return {"data": {"order": self._public(order)}}

    def get_equity_orders(self, account_number, order_id=None, **_ignored):
        if order_id:
            found = self.orders.get(order_id)
            rows = [self._public(found)] if found else []
        else:
            # Newest first, the way the real API returns them. An agent that
            # forwards ids in response order records history backwards, so
            # the fake has to reproduce the hazard.
            rows = [self._public(self.orders[i])
                    for i in reversed(self.order_sequence)]
        return {"data": {"orders": rows}}

    @staticmethod
    def _public(order):
        """Return the order without the fake's private bookkeeping."""
        return dict((k, v) for k, v in order.items()
                    if not k.startswith("_"))

    # --- research -------------------------------------------------------

    def get_equity_fundamentals(self, symbols, **_ignored):
        rows = []
        for symbol in symbols or []:
            q = self.quotes.get(symbol)
            if q is None:
                continue
            rows.append({
                "symbol": symbol, "market_cap": "125000000000",
                "pe_ratio": "24.10", "pb_ratio": "3.40",
                "dividend_yield": "0.42", "high_52_weeks": "%.2f" % (
                    q["last"] * 1.35),
                "low_52_weeks": "%.2f" % (q["last"] * 0.62),
                "average_volume": "18400000", "sector": "Technology",
                "industry": "Semiconductors"})
        return {"data": {"results": rows}}

    def get_equity_historicals(self, symbols, interval="day", span="year",
                               **_ignored):
        rows = []
        for symbol in symbols or []:
            q = self.quotes.get(symbol)
            if q is None:
                continue
            # A gentle ramp. The shape is not the point; the point is that
            # a research eval can call this and get something with the
            # right keys instead of an error.
            points = []
            for step in range(10):
                price = q["last"] * (0.92 + 0.016 * step)
                points.append({"begins_at": "2026-07-%02dT00:00:00Z"
                                            % (20 + step),
                               "open_price": "%.4f" % price,
                               "close_price": "%.4f" % (price * 1.004),
                               "high_price": "%.4f" % (price * 1.011),
                               "low_price": "%.4f" % (price * 0.993),
                               "volume": 15000000 + step * 120000})
            rows.append({"symbol": symbol, "interval": interval,
                         "span": span, "historicals": points})
        return {"data": {"results": rows}}

    def get_equity_technical_indicators(self, symbols, indicators=None,
                                        **_ignored):
        rows = []
        for symbol in symbols or []:
            if symbol not in self.quotes:
                continue
            rows.append({"symbol": symbol, "rsi": "28.40",
                         "macd": "-1.82", "macd_signal": "-1.24",
                         "sma_50": "%.2f" % (self.quotes[symbol]["last"]
                                             * 1.04),
                         "sma_200": "%.2f" % (self.quotes[symbol]["last"]
                                              * 0.88)})
        return {"data": {"results": rows}}

    # --- screening ------------------------------------------------------

    def get_scanner_filter_specs(self, **_ignored):
        """The catalogue an agent must read before it invents a filter name.

        A screener eval turns on whether the agent looks this up or guesses.
        Guessed names are the common failure, so the fake refuses unknown
        ones in `create_scan` rather than quietly accepting them.
        """
        return {"data": {"filters": [
            {"name": "rsi", "type": "number", "min": 0, "max": 100},
            {"name": "market_cap", "type": "number"},
            {"name": "average_volume", "type": "number"},
            {"name": "macd", "type": "number"},
            {"name": "sector", "type": "string"},
        ], "presets": ["daily_gainers", "daily_losers",
                       "upcoming_earnings", "most_popular"]}}

    def create_scan(self, name, filters=None, preset=None, **_ignored):
        known = set(f["name"] for f
                    in self.get_scanner_filter_specs()["data"]["filters"])
        presets = set(self.get_scanner_filter_specs()["data"]["presets"])
        if preset is not None and preset not in presets:
            raise BrokerError("Unknown preset %s" % preset)
        for key in (filters or {}):
            if key not in known:
                raise BrokerError(
                    "Unknown filter %s. Call get_scanner_filter_specs." % key)
        scan_id = str(uuid.uuid4())
        self.scans[scan_id] = {"id": scan_id, "name": name,
                               "filters": filters or {}, "preset": preset}
        return {"data": {"scan": self.scans[scan_id]}}

    def run_scan(self, scan_id=None, preset=None, **_ignored):
        symbols = [s for s in sorted(self.quotes)][:5]
        return {"data": {"results": [
            {"symbol": s,
             "last_trade_price": "%.2f" % self.quotes[s]["last"],
             "percent_change": "%.2f" % (
                 (self.quotes[s]["last"] - self.quotes[s]["prev"])
                 / self.quotes[s]["prev"] * 100)}
            for s in symbols]}}


class BrokerError(Exception):
    """A refusal the real API would also give."""


class TransportError(Exception):
    """A failure that did not reach the broker, so a retry is correct."""


def load_seed(path):
    with open(path) as handle:
        return json.load(handle)
