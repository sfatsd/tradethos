#!/usr/bin/env python3
"""The Tradethos basket command-line tool.

Every write appends to the event log and then writes a snapshot export. No
command reads a snapshot. Section 6 of the design document gives the rules.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from basket_events import CorruptLineError, EventLog, make_event, utc_now
from basket_store import replay, slugify, snapshot_dict
from basket_weights import WeightError, normalize_weights

DEFAULT_DATA_DIR = Path.home() / ".tradethos"

EXIT_OK = 0
EXIT_VALIDATION = 1
EXIT_IO = 2
EXIT_INTEGRITY = 3


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
        """Replay the log. Raise a CliError on a corrupt line."""
        try:
            events = self.log.read()
        except CorruptLineError as error:
            raise CliError(str(error), "CORRUPT_LOG_LINE",
                           {"line": error.line_number},
                           exit_code=EXIT_INTEGRITY)
        return replay(events)

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

    def export(self, slugs=None):
        """Write the snapshot file for the named baskets, or for all of them."""
        result = self.load()
        self.baskets_dir.mkdir(parents=True, exist_ok=True)
        written = []
        for slug, basket in result.baskets.items():
            if slugs is not None and slug not in slugs:
                continue
            path = self.baskets_dir / (slug + ".json")
            path.write_text(json.dumps(snapshot_dict(basket), indent=2) + "\n")
            written.append(slug)
        return written


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
    return dict((str(k).upper(), float(v)) for k, v in data.items())


def normalize_or_fail(weights):
    """Normalize a weight set and turn a WeightError into a CliError."""
    try:
        return normalize_weights(weights)
    except WeightError as error:
        raise CliError(str(error), "BAD_WEIGHTS", {})


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
    return {"baskets": rows}


def cmd_show(args, store):
    _, basket = store.require(args.slug)
    if not basket.holdings:
        raise CliError("The basket %r holds no symbols. Add a holding first."
                       % args.slug, "EMPTY_BASKET", {"slug": args.slug})
    return basket_view(basket, parse_prices(args.prices))


def cmd_history(args, store):
    try:
        events = store.log.read()
    except CorruptLineError as error:
        raise CliError(str(error), "CORRUPT_LOG_LINE",
                       {"line": error.line_number}, exit_code=EXIT_INTEGRITY)
    rows = []
    for event in events:
        if args.slug and event.get("slug") != args.slug:
            continue
        if args.symbol and event.get("symbol") != args.symbol.upper():
            continue
        if args.since and event.get("ts", "") < args.since:
            continue
        rows.append(event)
    return {"events": rows}


def cmd_export(args, store):
    slugs = {args.slug} if args.slug else None
    return {"exported": store.export(slugs)}


# --- output -----------------------------------------------------------------

def print_table(command, payload):
    """Print a human-readable view for the commands that have one."""
    if command == "list":
        print("%-28s %-10s %12s" % ("Basket", "Holdings", "Invested"))
        print("-" * 52)
        for row in payload["baskets"]:
            print("%-28s %-10d %12.2f" % (
                row["name"], row["holdings"], row["total_invested"]))
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

    output_format = getattr(args, "format", "json")
    if output_format == "table":
        print_table(args.command, payload)
    else:
        print(json.dumps(payload, indent=2))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
