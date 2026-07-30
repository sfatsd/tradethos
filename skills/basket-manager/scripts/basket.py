#!/usr/bin/env python3
"""The Tradethos basket command-line tool.

Every write appends to the event log and then writes a snapshot export. No
command reads a snapshot: the log is the only source of truth, and a snapshot
is a derived view that can be deleted at any time and rebuilt by a replay. A
basket's target weights are whole numbers that always sum to exactly 100, and
every write goes through one code path, under one lock, so that invariant
never breaks mid-write.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import basket_events
from basket_events import (EPOCH, EventLog, corrupt_line_warnings, make_event,
                           parse_timestamp, utc_now)
from basket_store import replay, slugify, snapshot_dict
from basket_weights import FILL_MODES, WeightError, normalize_weights, refill

DEFAULT_DATA_DIR = Path.home() / ".tradethos"

EXIT_OK = 0
EXIT_VALIDATION = 1
EXIT_IO = 2
EXIT_INTEGRITY = 3

# A command that must print a full report AND still signal a non-zero exit
# puts the code under this key. main() removes it before printing, so it never
# reaches the caller's JSON.
EXIT_KEY = "_exit_code"


class CliError(Exception):
    """An error that the tool reports as JSON on stderr."""

    def __init__(self, message, code, detail=None, exit_code=EXIT_VALIDATION):
        super(CliError, self).__init__(message)
        self.message = message
        self.code = code
        self.detail = detail or {}
        self.exit_code = exit_code


class Store(object):
    """Bind the log and the snapshot directory to one data directory."""

    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.log = EventLog(self.data_dir)
        self.baskets_dir = self.data_dir / "baskets"

    def load(self):
        """Replay the log, skipping any line that will not parse.

        One truncated line - what a crash mid-flush leaves behind - must not
        make every basket in the store unreadable. The corrupt lines ride
        along on the result so `verify` can report them.
        """
        events, corrupt = self.log.read_with_corruption()
        return replay(events, corrupt)

    def require(self, slug):
        """Return one basket, or raise a CliError that lists the slugs."""
        result = self.load()
        basket = result.baskets.get(slug)
        if basket is None:
            raise CliError(
                "No basket has the slug %r" % slug, "BASKET_NOT_FOUND",
                {"slug": slug, "available": sorted(result.baskets)})
        return result, basket

    def write(self, events, slugs):
        """Append events, then export the snapshots for the changed baskets."""
        self.log.append(events)
        self.export(slugs)
        maybe_backup(self)

    def export(self, slugs=None):
        """Write the snapshot file for the named baskets, or for all of them.

        Returns (written, corrupt): the slugs written, and any log lines the
        replay had to skip, so a caller can report them.
        """
        result = self.load()
        self.baskets_dir.mkdir(parents=True, exist_ok=True)
        written = []
        for slug, basket in result.baskets.items():
            if slugs is not None and slug not in slugs:
                continue
            path = self.baskets_dir / (slug + ".json")
            path.write_text(json.dumps(snapshot_dict(basket), indent=2) + "\n")
            written.append(slug)
        return written, result.corrupt


def parse_symbol_weights(text):
    """Parse 'NVDA:2,MSFT:1' into {'NVDA': 2.0, 'MSFT': 1.0}."""
    weights = {}
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            raise CliError(
                "Expected SYMBOL:WEIGHT, got %r" % chunk, "BAD_SYMBOL_LIST",
                {"item": chunk})
        symbol, _, raw = chunk.partition(":")
        symbol = symbol.strip().upper()
        try:
            value = float(raw)
        except ValueError:
            raise CliError(
                "The weight for %s is not a number: %r" % (symbol, raw),
                "BAD_WEIGHT", {"symbol": symbol, "value": raw})
        weights[symbol] = value
    if not weights:
        raise CliError("No symbols given", "BAD_SYMBOL_LIST", {})
    return weights


def parse_prices(text):
    """Parse a --prices JSON object into {symbol: float}."""
    if not text:
        return {}
    try:
        data = json.loads(text)
    except ValueError as error:
        raise CliError("The --prices value is not valid JSON: %s" % error,
                       "BAD_PRICES", {})
    if not isinstance(data, dict):
        raise CliError("The --prices value must be a JSON object",
                       "BAD_PRICES", {})

    prices = {}
    for key, value in data.items():
        symbol = str(key).upper()
        try:
            prices[symbol] = float(value)
        except (TypeError, ValueError):
            # A traceback here would break every caller, which parses the
            # JSON error envelope on stderr.
            raise CliError(
                "The price for %s is not a number: %r" % (symbol, value),
                "BAD_PRICE", {"symbol": symbol, "value": value})
    return prices


def normalize_or_fail(weights):
    """Normalize a weight set and turn a WeightError into a CliError."""
    try:
        return normalize_weights(weights)
    except WeightError as error:
        raise CliError(str(error), "BAD_WEIGHTS", {})


def refill_or_fail(others, room, mode):
    """Call refill and turn a WeightError into a CliError."""
    try:
        return refill(others, room, mode)
    except WeightError as error:
        raise CliError(str(error), "BAD_WEIGHTS", {})


def check_target_weight(weight, symbol):
    """Reject a target weight outside 1 to 100.

    argparse accepts any integer, so 0 and negatives reach the command. A
    weight of 0 means the user wants the holding gone, and remove-holding is
    the command for that.
    """
    if weight < 1 or weight > 100:
        raise CliError(
            "A target weight must be between 1 and 100, but %s was given %d. "
            "Use remove-holding to drop a holding." % (symbol, weight),
            "BAD_WEIGHTS", {"symbol": symbol, "weight": weight})


def plan_weight_change(basket, target_symbol, target_weight, fill_mode,
                       removing=False):
    """Return the complete weight set after a single-holding change.

    The named holding takes its weight. Every other holding shares the rest,
    by the fill mode. The result always sums to 100.

    With removing=True the named holding leaves, and the remaining holdings
    share the whole 100 percent.
    """
    others = dict((s, w) for s, w in basket.target_weights.items()
                  if s != target_symbol)

    if removing:
        if not others:
            return {}
        return refill_or_fail(others, 100, fill_mode)

    if not others:
        return {target_symbol: 100}

    room = 100 - int(target_weight)
    if room < 1:
        raise CliError(
            "A weight of %d leaves nothing for the other holdings"
            % int(target_weight), "BAD_WEIGHTS", {"symbol": target_symbol})

    filled = refill_or_fail(others, room, fill_mode)
    filled[target_symbol] = int(target_weight)
    return filled


def weight_events(basket, new_weights):
    """Build a weight_changed event for each holding whose weight moved."""
    events = []
    for symbol, weight in new_weights.items():
        holding = basket.holdings.get(symbol)
        if holding is None or holding.target_weight_pct == weight:
            continue
        events.append(make_event(
            "weight_changed", basket.slug, symbol=symbol,
            **{"from": holding.target_weight_pct, "to": weight}))
    return events


def basket_view(basket, prices=None):
    """Build the JSON view that `show` prints."""
    prices = prices or {}
    data = snapshot_dict(basket)
    total_value = 0.0
    for entry in data["holdings"]:
        price = prices.get(entry["symbol"])
        entry["current_price"] = price
        position = entry["position"]
        if position and price is not None:
            value = position["shares"] * price
            entry["current_value"] = round(value, 2)
            entry["pnl"] = round(value - position["total_invested"], 2)
            total_value += value
        else:
            entry["current_value"] = None
            entry["pnl"] = None
    data["totals"]["current_value"] = round(total_value, 2) if prices else None
    return data


# --- commands ---------------------------------------------------------------

def cmd_create(args, store):
    slug = slugify(args.name)
    if not slug:
        raise CliError("The name gives an empty slug", "BAD_NAME",
                       {"name": args.name})

    raw = parse_symbol_weights(args.symbols)
    normalized = normalize_or_fail(raw)

    with store.log.locked():
        result = store.load()
        if slug in result.baskets:
            raise CliError("A basket already uses the slug %r" % slug,
                           "SLUG_EXISTS", {"slug": slug})

        events = [make_event(
            "basket_created", slug, name=args.name,
            description=args.description or "",
            account_number=args.account,
            rebalance_threshold_pct=args.threshold)]
        for symbol in raw:
            events.append(make_event(
                "holding_added", slug, symbol=symbol,
                weight=normalized[symbol], thesis=""))
        store.write(events, {slug})

    _, basket = store.require(slug)
    view = snapshot_dict(basket)
    view["normalized"] = {s: normalized[s] for s in raw if raw[s] != normalized[s]}
    return view


def cmd_list(args, store):
    result = store.load()
    rows = []
    for slug, basket in result.baskets.items():
        rows.append({
            "slug": slug,
            "name": basket.name,
            "holdings": len(basket.holdings),
            "total_invested": round(basket.total_invested, 2),
            "realized_pnl": round(basket.realized_pnl, 2),
        })
    payload = {"baskets": rows}
    if result.corrupt:
        payload["warnings"] = corrupt_line_warnings(result.corrupt)
    return payload


def cmd_show(args, store):
    result, basket = store.require(args.slug)
    if not basket.holdings:
        raise CliError("The basket %r holds no symbols. Add a holding first."
                       % args.slug, "EMPTY_BASKET", {"slug": args.slug})
    view = basket_view(basket, parse_prices(args.prices))
    if result.corrupt:
        view["warnings"] = corrupt_line_warnings(result.corrupt)
    return view


def cmd_history(args, store):
    # A corrupt line is skipped, not fatal: the history of every other basket
    # stays readable. It still surfaces here in `warnings`, so a user reading
    # history knows the record is incomplete.
    events, corrupt = store.log.read_with_corruption()
    rows = []
    for event in events:
        if args.slug and event.get("slug") != args.slug:
            continue
        if args.symbol and event.get("symbol") != args.symbol.upper():
            continue
        if args.since and event.get("ts", "") < args.since:
            continue
        rows.append(event)
    payload = {"events": rows}
    if corrupt:
        payload["warnings"] = corrupt_line_warnings(corrupt)
    return payload


def cmd_export(args, store):
    slugs = {args.slug} if args.slug else None
    written, corrupt = store.export(slugs)
    payload = {"exported": written}
    if corrupt:
        payload["warnings"] = corrupt_line_warnings(corrupt)
    return payload


def _apply_weights(store, args, slug, basket, new_weights, extra_events=()):
    """Write one batch of events, or return the dry-run view.

    Every weight command appends exactly once. Two appends would leave a
    window in which the log breaks the invariant that a basket's target
    weights always sum to exactly 100.
    """
    events = list(extra_events) + weight_events(basket, new_weights)
    if args.dry_run:
        return {"dry_run": True, "slug": slug, "weights": new_weights,
                "events": len(events)}
    if events:
        store.write(events, {slug})
    return {"dry_run": False, "slug": slug, "weights": new_weights}


def cmd_set_weight(args, store):
    with store.log.locked():
        _, basket = store.require(args.slug)
        symbol = args.symbol.upper()
        if symbol not in basket.holdings:
            raise CliError("The basket does not hold %s" % symbol,
                           "SYMBOL_NOT_FOUND", {"symbol": symbol})
        check_target_weight(args.weight, symbol)
        new_weights = plan_weight_change(basket, symbol, args.weight, args.fill)
        return _apply_weights(store, args, args.slug, basket, new_weights)


def cmd_set_weights(args, store):
    with store.log.locked():
        _, basket = store.require(args.slug)
        given = parse_symbol_weights(args.weights)

        missing = sorted(set(basket.holdings) - set(given))
        if missing:
            raise CliError(
                "These holdings have no weight: %s" % ", ".join(missing),
                "MISSING_WEIGHTS", {"missing": missing})
        unknown = sorted(set(given) - set(basket.holdings))
        if unknown:
            raise CliError(
                "The basket does not hold: %s" % ", ".join(unknown),
                "SYMBOL_NOT_FOUND", {"unknown": unknown})

        return _apply_weights(store, args, args.slug, basket,
                              normalize_or_fail(given))


def cmd_add_holding(args, store):
    with store.log.locked():
        _, basket = store.require(args.slug)
        symbol = args.symbol.upper()
        if symbol in basket.holdings:
            raise CliError("The basket already holds %s" % symbol,
                           "SYMBOL_EXISTS", {"symbol": symbol})
        if len(basket.holdings) + 1 > 100:
            raise CliError("A basket holds at most 100 holdings",
                           "TOO_MANY_HOLDINGS", {})
        check_target_weight(args.weight, symbol)

        new_weights = plan_weight_change(basket, symbol, args.weight, args.fill)
        added = make_event("holding_added", args.slug, symbol=symbol,
                           weight=int(args.weight), thesis=args.thesis or "")
        # weight_events skips the new symbol, because the basket does not hold
        # it yet. The holding_added event carries its weight instead.
        return _apply_weights(store, args, args.slug, basket, new_weights,
                              extra_events=[added])


def cmd_remove_holding(args, store):
    with store.log.locked():
        _, basket = store.require(args.slug)
        symbol = args.symbol.upper()
        holding = basket.holdings.get(symbol)
        if holding is None:
            raise CliError("The basket does not hold %s" % symbol,
                           "SYMBOL_NOT_FOUND", {"symbol": symbol})
        realized = holding.position.realized_pnl
        if holding.has_position and not args.force:
            raise CliError(
                "%s still holds %s shares. Sell them first, or pass --force."
                % (symbol, holding.position.shares),
                "HOLDING_HAS_POSITION",
                {"symbol": symbol, "shares": holding.position.shares})
        # has_position is shares > 0, so a holding that was bought and then
        # sold out to zero passes that check while still carrying every dollar
        # of realized profit or loss it ever made. Removing it drops that
        # record, and a real gain or loss disappears from the basket totals.
        if realized != 0 and not args.force:
            raise CliError(
                "%s holds no shares, but it carries %.2f of realized profit "
                "and loss. Removing it would discard that record. Pass "
                "--force to remove it anyway." % (symbol, realized),
                "HOLDING_HAS_REALIZED_PNL",
                {"symbol": symbol, "realized_pnl": round(realized, 2)})

        new_weights = plan_weight_change(basket, symbol, 0, args.fill,
                                         removing=True)
        removed = make_event("holding_removed", args.slug, symbol=symbol)
        # new_weights omits the removed symbol, so weight_events never emits
        # an event for it.
        payload = _apply_weights(store, args, args.slug, basket, new_weights,
                                 extra_events=[removed])
        if realized != 0:
            payload["warnings"] = [
                "Removing %s discarded %.2f of realized profit and loss."
                % (symbol, realized)]
        return payload


def cmd_set_name(args, store):
    with store.log.locked():
        _, basket = store.require(args.slug)
        event = make_event("basket_updated", args.slug, field="name",
                           **{"from": basket.name, "to": args.name})
        store.write([event], {args.slug})
    return {"slug": args.slug, "name": args.name}


def cmd_set_description(args, store):
    with store.log.locked():
        _, basket = store.require(args.slug)
        event = make_event("basket_updated", args.slug, field="description",
                           **{"from": basket.description, "to": args.description})
        store.write([event], {args.slug})
    return {"slug": args.slug, "description": args.description}


def cmd_set_threshold(args, store):
    with store.log.locked():
        _, basket = store.require(args.slug)
        event = make_event(
            "basket_updated", args.slug, field="rebalance_threshold_pct",
            **{"from": basket.rebalance_threshold_pct, "to": args.threshold})
        store.write([event], {args.slug})
    return {"slug": args.slug, "rebalance_threshold_pct": args.threshold}


def cmd_set_thesis(args, store):
    with store.log.locked():
        _, basket = store.require(args.slug)
        symbol = args.symbol.upper()
        if symbol not in basket.holdings:
            raise CliError("The basket does not hold %s" % symbol,
                           "SYMBOL_NOT_FOUND", {"symbol": symbol})
        event = make_event("thesis_changed", args.slug, symbol=symbol,
                           thesis=args.thesis)
        store.write([event], {args.slug})
    return {"slug": args.slug, "symbol": symbol, "thesis": args.thesis}


def cmd_delete(args, store):
    with store.log.locked():
        _, basket = store.require(args.slug)
        held = [h.symbol for h in basket.holdings.values() if h.has_position]
        if held and not args.force:
            raise CliError(
                "The basket still holds %s. Deletion removes the record only; "
                "it does not sell any stock. Pass --force to delete it."
                % ", ".join(held),
                "BASKET_HAS_POSITIONS",
                {"symbols": held, "total_invested": basket.total_invested})
        # The same leak as remove-holding, one level up: a basket sold out to
        # zero shares still carries its realized profit and loss, and deleting
        # it discards that record.
        realized = basket.realized_pnl
        if realized != 0 and not args.force:
            raise CliError(
                "The basket holds no shares, but it carries %.2f of realized "
                "profit and loss. Deleting it would discard that record. Pass "
                "--force to delete it anyway." % realized,
                "BASKET_HAS_REALIZED_PNL",
                {"slug": args.slug, "realized_pnl": round(realized, 2)})
        store.log.append([make_event("basket_deleted", args.slug)])
        maybe_backup(store)

    path = store.baskets_dir / (args.slug + ".json")
    if path.exists():
        path.unlink()
    payload = {"deleted": args.slug}
    if realized != 0:
        payload["warnings"] = [
            "Deleting %s discarded %.2f of realized profit and loss."
            % (args.slug, realized)]
    return payload


def _require_holdings(basket):
    if not basket.holdings:
        raise CliError(
            "The basket %r holds no symbols. Add a holding first." % basket.slug,
            "EMPTY_BASKET", {"slug": basket.slug})


def cmd_plan_buy(args, store):
    _, basket = store.require(args.slug)
    _require_holdings(basket)
    prices = parse_prices(args.prices)

    orders = []
    for symbol, holding in basket.holdings.items():
        amount = args.amount * holding.target_weight_pct / 100.0
        row = {"symbol": symbol,
               "target_weight_pct": holding.target_weight_pct,
               "amount": round(amount, 2), "shares": None}
        price = prices.get(symbol)
        if price:
            row["shares"] = round(amount / price, 6)
            row["price"] = price
        orders.append(row)

    return {"slug": args.slug, "side": "buy", "amount": args.amount,
            "orders": orders}


def cmd_plan_sell(args, store):
    _, basket = store.require(args.slug)
    _require_holdings(basket)
    prices = parse_prices(args.prices)

    held = [(s, h) for s, h in basket.holdings.items() if h.has_position]

    if args.all:
        if not held:
            raise CliError("The basket holds no shares", "NO_POSITION",
                           {"slug": args.slug})
        orders = []
        proceeds = 0.0
        for symbol, holding in held:
            row = {"symbol": symbol, "shares": round(holding.position.shares, 6)}
            price = prices.get(symbol)
            if price:
                value = holding.position.shares * price
                row["price"] = price
                row["estimated_proceeds"] = round(value, 2)
                proceeds += value
            orders.append(row)
        return {"slug": args.slug, "side": "sell", "mode": "all",
                "orders": orders,
                "estimated_proceeds": round(proceeds, 2) if prices else None}

    # --amount checks --prices before checking for a held position, so a call
    # with neither reports the missing prices rather than the empty position.
    if not prices:
        raise CliError("--amount needs --prices, because the split follows the "
                       "current market value", "PRICES_REQUIRED", {})

    if not held:
        raise CliError("The basket holds no shares", "NO_POSITION",
                       {"slug": args.slug})

    values = {}
    total_value = 0.0
    for symbol, holding in held:
        price = prices.get(symbol)
        if not price:
            raise CliError("No price given for %s" % symbol, "PRICES_REQUIRED",
                           {"symbol": symbol})
        value = holding.position.shares * price
        values[symbol] = value
        total_value += value

    if args.amount > total_value + 1e-9:
        raise CliError(
            "The basket is worth %.2f, which is below the requested %.2f. "
            "Use --all to exit the basket." % (total_value, args.amount),
            "AMOUNT_ABOVE_VALUE",
            {"basket_value": round(total_value, 2), "requested": args.amount})

    fraction = args.amount / total_value
    orders = []
    for symbol, holding in held:
        shares = holding.position.shares * fraction
        orders.append({"symbol": symbol, "shares": round(shares, 6),
                       "price": prices[symbol],
                       "estimated_proceeds": round(shares * prices[symbol], 2)})
    return {"slug": args.slug, "side": "sell", "mode": "amount",
            "amount": args.amount, "basket_value": round(total_value, 2),
            "orders": orders}


def orders_from_response(payload):
    """Return {order_id: order} from a get_equity_orders response.

    The live response nests the list under a `data` key. A bare list and a
    single-level envelope also work.
    """
    try:
        data = json.loads(payload)
    except ValueError as error:
        raise CliError("The --orders-json value is not valid JSON: %s" % error,
                       "BAD_ORDERS_JSON", {})

    if isinstance(data, dict) and "data" in data:
        data = data["data"]
    if isinstance(data, dict):
        for key in ("orders", "results"):
            if key in data:
                data = data[key]
                break
    if not isinstance(data, list):
        raise CliError(
            "Expected a list of orders. Pass the get_equity_orders response.",
            "BAD_ORDERS_JSON", {})

    index = {}
    for entry in data:
        if isinstance(entry, dict) and entry.get("id"):
            index[entry["id"]] = entry
    return index


def _order_shares(order):
    """Return the filled share count of an order."""
    for key in ("cumulative_quantity", "quantity"):
        raw = order.get(key)
        if raw in (None, ""):
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0.0


def _order_price(order):
    """Return the fill price of an order.

    The tool reads `average_price`. It never reads `price`, which holds the
    limit price. A weighted mean of the executions is the only fallback.
    """
    raw = order.get("average_price")
    if raw not in (None, ""):
        try:
            value = float(raw)
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass

    total_shares = 0.0
    total_cost = 0.0
    for execution in order.get("executions") or []:
        if not isinstance(execution, dict):
            continue
        try:
            shares = float(execution.get("quantity") or 0)
            price = float(execution.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if shares > 0 and price > 0:
            total_shares += shares
            total_cost += shares * price
    return total_cost / total_shares if total_shares > 0 else 0.0


def _order_fill_time(order):
    """Return the raw fill timestamp of an order, or None.

    `last_transaction_at` is the fill time and wins. `created_at` is the
    fallback, and it is also the fallback when `last_transaction_at` holds
    something no parser can read - a usable time in the second field beats an
    unusable one in the first. The event's `ts` and the sort key both come
    from here, so the log and the ordering can never disagree.
    """
    if not isinstance(order, dict):
        return None
    candidates = [order.get("last_transaction_at"), order.get("created_at")]
    for raw in candidates:
        if raw and parse_timestamp(raw) is not None:
            return raw
    for raw in candidates:
        if raw:
            return raw
    return None


def sort_order_ids(order_ids, index):
    """Return (sorted_ids, undated) with the ids in trade order.

    Average cost and realized profit and loss depend on the sequence the
    trades are applied in: a buy at 10, a sell at 15 and a buy at 20 give a
    different cost basis and a different realized figure for every ordering.
    The caller passes ids in whatever order it happened to have them, and
    `get_equity_orders` returns orders newest first, so an agent forwarding
    them in response order would record the history backwards.

    The sort key is the fill time, which is what the event's `ts` already
    carries. The raw strings are not comparable as text - they carry two to
    six fractional digits and may or may not end in `Z` - so each one is
    parsed. An id with no usable time sorts last and is reported, never
    silently dropped into an arbitrary slot. Ties keep the caller's order.
    """
    decorated = []
    undated = []
    for position, order_id in enumerate(order_ids):
        order = index.get(order_id)
        stamp = parse_timestamp(_order_fill_time(order))
        if stamp is None and order is not None:
            undated.append(order_id)
        decorated.append((stamp is None, stamp or EPOCH, position, order_id))
    decorated.sort(key=lambda row: (row[0], row[1], row[2]))
    return [row[3] for row in decorated], undated


def cmd_record_fills(args, store):
    given_ids = [i.strip() for i in args.order_ids.split(",") if i.strip()]
    if not given_ids:
        raise CliError("No order ids given", "NO_ORDER_IDS", {})

    # A repeated id in one batch would append two identical events. The replay
    # dedupes them, so the state stays right, but the log keeps the junk line
    # forever.
    order_ids = []
    repeated = []
    for order_id in given_ids:
        if order_id in order_ids:
            repeated.append(order_id)
            continue
        order_ids.append(order_id)

    if args.cap_at_held and len(order_ids) != 1:
        raise CliError(
            "--cap-at-held needs exactly one order id, but %d were given"
            % len(order_ids), "CAP_NEEDS_ONE_ORDER", {"count": len(order_ids)})

    index = orders_from_response(args.orders_json)
    order_ids, undated = sort_order_ids(order_ids, index)

    with store.log.locked():
        result, basket = store.require(args.slug)

        if basket.account_number and args.account != basket.account_number:
            raise CliError(
                "The basket uses account %s, but the orders came from %s"
                % (basket.account_number, args.account),
                "ACCOUNT_MISMATCH",
                {"basket_account": basket.account_number, "given": args.account})

        events = []
        recorded = []
        already = []
        skipped = []
        capped = []
        late = []
        # Track shares within this batch so two sells in one call cannot
        # oversell the same holding, and a buy earlier in the batch can fund
        # a sell later in it.
        pending = dict((s, h.position.shares) for s, h in basket.holdings.items())
        # The latest fill time already on record for each symbol. The batch is
        # sorted, so anything older than this is history arriving late.
        latest = dict((s, h.last_fill_ts) for s, h in basket.holdings.items())

        for order_id in order_ids:
            order = index.get(order_id)
            if order is None:
                skipped.append({"order_id": order_id,
                                "reason": "ORDER_NOT_IN_RESPONSE"})
                continue
            if order.get("state") != "filled":
                skipped.append({"order_id": order_id, "reason": "NOT_FILLED",
                                "state": order.get("state")})
                continue

            owner = result.order_index.get(order_id)
            if owner == args.slug:
                already.append(order_id)
                continue
            if owner is not None:
                # Skip, do not abort. A batch keeps its good fills; the exit
                # code table gives exit 1 only when nothing was recorded at
                # all.
                skipped.append({"order_id": order_id,
                                "reason": "ORDER_IN_OTHER_BASKET",
                                "basket": owner})
                continue

            symbol = (order.get("symbol") or "").upper()
            if symbol not in basket.holdings:
                skipped.append({"order_id": order_id, "reason": "SYMBOL_NOT_IN_BASKET",
                                "symbol": symbol})
                continue

            shares = _order_shares(order)
            price = _order_price(order)
            if shares <= 0 or price <= 0:
                skipped.append({"order_id": order_id, "reason": "NO_SHARES_OR_PRICE"})
                continue

            side = (order.get("side") or "").lower()
            if side not in ("buy", "sell"):
                skipped.append({"order_id": order_id, "reason": "UNKNOWN_SIDE",
                                "side": side})
                continue

            if side == "sell":
                held = pending.get(symbol, 0.0)
                if shares > held + 1e-12:
                    if not args.cap_at_held:
                        skipped.append({
                            "order_id": order_id, "reason": "OVERSELL",
                            "symbol": symbol, "held": held, "requested": shares})
                        continue
                    capped.append({"order_id": order_id, "symbol": symbol,
                                   "order_shares": shares,
                                   "recorded_shares": held})
                    shares = held
                if shares <= 0:
                    skipped.append({"order_id": order_id, "reason": "OVERSELL",
                                    "symbol": symbol, "held": held,
                                    "requested": shares})
                    continue
                pending[symbol] = held - shares
            else:
                pending[symbol] = pending.get(symbol, 0.0) + shares

            fill_ts = _order_fill_time(order)
            # This fill is older than one already recorded for the same
            # symbol, so the log is being appended out of sequence. Record it
            # anyway - dropping a real fill is worse than an unordered log -
            # but report it so the agent can tell the user.
            stamp = parse_timestamp(fill_ts)
            previous = parse_timestamp(latest.get(symbol) or "")
            if stamp is not None and previous is not None and stamp < previous:
                late.append({"order_id": order_id, "symbol": symbol,
                             "fill_time": fill_ts,
                             "latest_recorded": latest.get(symbol)})
            elif stamp is not None and (previous is None or stamp >= previous):
                latest[symbol] = fill_ts

            events.append(make_event(
                side, args.slug, symbol=symbol,
                shares=shares, price=price, amount=shares * price,
                order_id=order_id, ts=fill_ts))
            recorded.append(order_id)

        if not recorded and not already:
            raise CliError(
                "No order was recorded", "NOTHING_RECORDED",
                {"skipped": skipped})

        if events:
            store.write(events, {args.slug})

    _, basket = store.require(args.slug)
    return {
        "slug": args.slug,
        "recorded": recorded,
        "already_recorded": already,
        "skipped": skipped,
        "capped": capped,
        "late_fills": late,
        "repeated_ids": repeated,
        "undated": undated,
        "holdings": snapshot_dict(basket)["holdings"],
    }


BACKUP_EVERY = 20
MARKER_NAME = "backup.marker"
BACKUP_DIR_NAME = "backups"


def positions_from_response(payload):
    """Return {symbol: shares} from a get_equity_positions response."""
    if not payload:
        return None
    try:
        data = json.loads(payload)
    except ValueError as error:
        raise CliError("The --positions value is not valid JSON: %s" % error,
                       "BAD_POSITIONS_JSON", {})
    if isinstance(data, dict) and "data" in data:
        data = data["data"]
    if isinstance(data, dict):
        for key in ("positions", "results"):
            if key in data:
                data = data[key]
                break
    if not isinstance(data, list):
        raise CliError("Expected a list of positions", "BAD_POSITIONS_JSON", {})

    out = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        symbol = (entry.get("symbol") or "").upper()
        if not symbol:
            continue
        try:
            out[symbol] = float(entry.get("quantity") or 0)
        except (TypeError, ValueError):
            continue
    return out


def read_marker(store):
    path = store.data_dir / MARKER_NAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except ValueError:
        return None


def run_backup(store, to=None):
    """Copy the log to a timestamped file and update the marker."""
    if not store.log.path.exists():
        return None
    backups = Path(to) if to else store.data_dir / BACKUP_DIR_NAME
    backups.mkdir(parents=True, exist_ok=True)
    with store.log.locked():
        count = store.log.count()
        stamp = utc_now().replace(":", "").replace("-", "").replace(".", "")
        target = backups / ("events-%s.jsonl" % stamp)
        shutil.copyfile(str(store.log.path), str(target))
    (store.data_dir / MARKER_NAME).write_text(json.dumps({
        "events": count, "at": utc_now(), "path": str(target)}) + "\n")
    return target


def maybe_backup(store):
    """Back the log up when it has grown past the threshold."""
    marker = read_marker(store)
    count = store.log.count()
    if marker is None or count - marker.get("events", 0) >= BACKUP_EVERY:
        run_backup(store)
        return True
    return False


def cmd_backup(args, store):
    target = run_backup(store, args.to)
    if target is None:
        raise CliError("There is no log to back up", "NO_LOG", {})
    return {"path": str(target), "events": store.log.count()}


def cmd_verify(args, store):
    result = store.load()
    warnings = []

    # A corrupt line is reported here rather than raised at load time, so the
    # report still names every basket that did replay. The command still exits
    # 3, because the store's integrity is in question.
    warnings.extend(corrupt_line_warnings(result.corrupt))

    if not basket_events.LOCKING_AVAILABLE:
        warnings.append(
            "This platform has no file locking, so the tool cannot protect "
            "the log against a concurrent write. Run one basket command at a "
            "time.")

    if args.slug and args.slug not in result.baskets:
        raise CliError("No basket has the slug %r" % args.slug,
                       "BASKET_NOT_FOUND",
                       {"slug": args.slug, "available": sorted(result.baskets)})

    def wanted(slug):
        return args.slug is None or slug == args.slug

    ignored = []
    for entry in result.ignored:
        if not wanted(entry["slug"]):
            continue
        ignored.append(entry)
        if entry["slug"] != entry["kept_by"]:
            warnings.append(
                "Line %s repeats order %s. Basket %s kept it, so basket %s "
                "lost that claim." % (entry["line"], entry["order_id"],
                                      entry["kept_by"], entry["slug"]))

    clamped = [c for c in result.clamped if wanted(c["slug"])]
    for entry in clamped:
        warnings.append(
            "Line %s sells %s shares of %s, but basket %s held fewer. The "
            "replay covered what it could and dropped %s shares."
            % (entry["line"], entry["requested"], entry["symbol"],
               entry["slug"], entry["oversold"]))

    marker = read_marker(store)
    count = store.log.count()
    if marker is None:
        warnings.append("No backup marker exists. Run `backup`.")
    elif count > marker.get("events", 0):
        warnings.append(
            "The log holds %d events, but the last backup held %d. Run `backup`."
            % (count, marker.get("events", 0)))

    positions = positions_from_response(args.positions)
    rows = None
    if positions is not None:
        # Claims are summed across EVERY live basket, regardless of `slug` -
        # comparing one basket's slice of a symbol against the whole account
        # is never meaningful; two baskets that each hold part of a symbol
        # can only be judged over-claimed by looking at their combined total.
        # `slug` narrows which symbols get a row (the ones a wanted basket
        # holds), never what a symbol's claimed total is.
        claims = {}
        holders = {}
        for slug, basket in result.baskets.items():
            for symbol, holding in basket.holdings.items():
                if holding.has_position:
                    claims[symbol] = claims.get(symbol, 0.0) + holding.position.shares
                    holders.setdefault(symbol, {})[slug] = holding.position.shares

        if args.slug:
            report_symbols = set()
            for slug, basket in result.baskets.items():
                if wanted(slug):
                    report_symbols.update(
                        s for s, h in basket.holdings.items() if h.has_position)
        else:
            report_symbols = set(claims) | set(positions)

        rows = []
        for symbol in sorted(report_symbols):
            claimed = claims.get(symbol, 0.0)
            actual = positions.get(symbol, 0.0)
            if abs(claimed - actual) < 1e-6:
                state = "match"
            elif claimed < actual:
                state = "outside_shares"
            else:
                state = "over_claimed"
                other_holders = sorted(
                    s for s in holders.get(symbol, {}) if s != args.slug)
                if args.slug and other_holders:
                    warnings.append(
                        "Baskets claim %s shares of %s, but the account "
                        "holds %s. %s also claims this symbol. You sold "
                        "shares a basket claims: record that sale against "
                        "whichever basket it came from, or buy the shares back."
                        % (claimed, symbol, actual, ", ".join(other_holders)))
                else:
                    warnings.append(
                        "Baskets claim %s shares of %s, but the account holds "
                        "%s. You sold shares this basket claims: record that "
                        "sale against the basket, or buy the shares back."
                        % (claimed, symbol, actual))
            rows.append({"symbol": symbol, "claimed": round(claimed, 6),
                         "account": round(actual, 6), "state": state})

    payload = {"baskets": sorted(s for s in result.baskets if wanted(s)),
               "events": count, "ignored_events": ignored,
               "clamped_sells": clamped, "corrupt_lines": result.corrupt,
               "locking_available": basket_events.LOCKING_AVAILABLE,
               "positions": rows, "warnings": warnings}
    if result.corrupt:
        payload["code"] = "CORRUPT_LOG_LINE"
        payload[EXIT_KEY] = EXIT_INTEGRITY
    return payload


# --- output -----------------------------------------------------------------

def print_table(command, payload):
    """Print a human-readable view for the commands that have one."""
    if command == "list":
        print("%-28s %-10s %12s" % ("Basket", "Holdings", "Invested"))
        print("-" * 52)
        for row in payload["baskets"]:
            print("%-28s %-10d %12.2f" % (
                row["name"], row["holdings"], row["total_invested"]))
        for warning in payload.get("warnings", []):
            print("WARNING: %s" % warning)
        return
    print(json.dumps(payload, indent=2))


def build_parser():
    # --data-dir and --format must work before and after the subcommand.
    # argparse hands everything after the subcommand name to the subparser, so
    # an option defined only on the root parser fails there with exit code 2.
    # Defaults are SUPPRESS, not None/"json", and deliberately so. argparse
    # parses a subcommand's arguments into a *fresh* sub-namespace and then
    # copies every key that sub-namespace defines back onto the parent
    # namespace - unconditionally, even for keys the subcommand's own
    # invocation never mentioned. A concrete default here (None or "json")
    # would fire on that fresh sub-namespace and clobber a --data-dir given
    # before the subcommand name with None. SUPPRESS keeps an option out of
    # the sub-namespace entirely when it is not given to the subparser, so
    # the merge leaves the parent's real value alone. main() reads these
    # back with getattr(..., default) to restore the effective default.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data-dir", default=argparse.SUPPRESS,
                        help=argparse.SUPPRESS)
    common.add_argument("--format", choices=["json", "table"],
                        default=argparse.SUPPRESS)

    parser = argparse.ArgumentParser(
        prog="basket.py", parents=[common],
        description="Manage local stock baskets")
    sub = parser.add_subparsers(dest="command")

    def add(name, help_text):
        return sub.add_parser(name, parents=[common], help=help_text)

    p = add("create", "Create a basket")
    p.add_argument("name")
    p.add_argument("--symbols", required=True, help="NVDA:2,MSFT:1")
    p.add_argument("--description", default="")
    p.add_argument("--account", required=True,
                   help="The brokerage account that will hold these trades")
    p.add_argument("--threshold", type=float, default=5.0)
    p.set_defaults(func=cmd_create)

    p = add("list", "List every basket")
    p.set_defaults(func=cmd_list)

    p = add("show", "Show one basket")
    p.add_argument("slug")
    p.add_argument("--prices", default="")
    p.set_defaults(func=cmd_show)

    p = add("history", "Print the events of a basket")
    p.add_argument("slug", nargs="?", default=None)
    p.add_argument("--symbol", default=None)
    p.add_argument("--since", default=None)
    p.set_defaults(func=cmd_history)

    p = add("export", "Write the snapshot files")
    p.add_argument("slug", nargs="?", default=None)
    p.set_defaults(func=cmd_export)

    def add_dry_run(p):
        p.add_argument("--dry-run", action="store_true")

    def add_fill(p):
        p.add_argument("--fill", choices=list(FILL_MODES), default=FILL_MODES[0])

    p = add("set-weight", "Set one target weight")
    p.add_argument("slug")
    p.add_argument("--symbol", required=True)
    p.add_argument("--weight", type=int, required=True)
    add_fill(p)
    add_dry_run(p)
    p.set_defaults(func=cmd_set_weight)

    p = add("set-weights", "Set every target weight")
    p.add_argument("slug")
    p.add_argument("--weights", required=True)
    add_dry_run(p)
    p.set_defaults(func=cmd_set_weights)

    p = add("add-holding", "Add a symbol")
    p.add_argument("slug")
    p.add_argument("--symbol", required=True)
    p.add_argument("--weight", type=int, required=True)
    p.add_argument("--thesis", default="")
    add_fill(p)
    add_dry_run(p)
    p.set_defaults(func=cmd_add_holding)

    p = add("remove-holding", "Remove a symbol")
    p.add_argument("slug")
    p.add_argument("--symbol", required=True)
    p.add_argument("--force", action="store_true")
    add_fill(p)
    add_dry_run(p)
    p.set_defaults(func=cmd_remove_holding)

    p = add("set-thesis", "Change a thesis")
    p.add_argument("slug")
    p.add_argument("--symbol", required=True)
    p.add_argument("--thesis", required=True)
    p.set_defaults(func=cmd_set_thesis)

    p = add("set-name", "Change the display name")
    p.add_argument("slug")
    p.add_argument("--name", required=True)
    p.set_defaults(func=cmd_set_name)

    p = add("set-description", "Change the description")
    p.add_argument("slug")
    p.add_argument("--description", required=True)
    p.set_defaults(func=cmd_set_description)

    p = add("set-threshold", "Change the rebalance threshold")
    p.add_argument("slug")
    p.add_argument("--threshold", type=float, required=True)
    p.set_defaults(func=cmd_set_threshold)

    p = add("delete", "Delete a basket")
    p.add_argument("slug")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_delete)

    p = add("record-fills", "Record filled orders")
    p.add_argument("slug")
    p.add_argument("--orders-json", required=True)
    p.add_argument("--order-ids", required=True)
    p.add_argument("--account", required=True)
    p.add_argument("--cap-at-held", action="store_true")
    p.set_defaults(func=cmd_record_fills)

    p = add("plan-buy", "Plan a whole-basket purchase")
    p.add_argument("slug")
    p.add_argument("--amount", type=float, required=True)
    p.add_argument("--prices", default="")
    p.set_defaults(func=cmd_plan_buy)

    p = add("plan-sell", "Plan a whole-basket sale")
    p.add_argument("slug")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--amount", type=float)
    group.add_argument("--all", action="store_true")
    p.add_argument("--prices", default="")
    p.set_defaults(func=cmd_plan_sell)

    p = add("verify", "Check the log against the account")
    p.add_argument("slug", nargs="?", default=None)
    p.add_argument("--positions", default="")
    p.set_defaults(func=cmd_verify)

    p = add("backup", "Copy the event log")
    p.add_argument("--to", default=None)
    p.set_defaults(func=cmd_backup)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return EXIT_VALIDATION

    raw_data_dir = getattr(args, "data_dir", None)
    data_dir = Path(raw_data_dir) if raw_data_dir else DEFAULT_DATA_DIR
    store = Store(data_dir)

    try:
        payload = args.func(args, store)
    except CliError as error:
        sys.stderr.write(json.dumps({
            "error": error.message,
            "code": error.code,
            "detail": error.detail,
        }) + "\n")
        return error.exit_code
    except OSError as error:
        sys.stderr.write(json.dumps({
            "error": str(error), "code": "IO_ERROR", "detail": {},
        }) + "\n")
        return EXIT_IO

    exit_code = EXIT_OK
    if isinstance(payload, dict):
        exit_code = payload.pop(EXIT_KEY, EXIT_OK)

    output_format = getattr(args, "format", "json")
    if output_format == "table":
        print_table(args.command, payload)
    else:
        print(json.dumps(payload, indent=2))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
