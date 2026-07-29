#!/usr/bin/env python3
"""Stage 2 migration: move the user's live cloud baskets into the local store.

Reads three inputs as JSON — the `get_watchlists` response, the
`get_equity_orders` response, and the legacy `data/baskets/*.json` files —
and writes events through `basket_events`. It never opens the event log
itself, and it never touches the network: everything arrives as arguments.

Order data is authoritative for shares and prices. The `Z64:` snapshot
embedded in each watchlist's description is a lossy 5-decimal copy; it is
used only to decide which basket an order belongs to. See
docs/superpowers/plans/2026-07-29-local-basket-storage-stage2.md.
"""

import argparse
import base64
import datetime
import json
import re
import sys
import zlib
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from basket_events import EPOCH, EventLog, make_event, parse_timestamp
from basket_store import replay, slugify
from basket_weights import WeightError, normalize_weights

DEFAULT_DATA_DIR = Path.home() / ".tradethos"

# The old cloud format truncated a basket's slug to this many characters, so
# it would fit the compressed metadata into a watchlist description. This
# migration is the last reader of that format; the limit stays here only to
# recognize a truncated slug. See resolve_slug below.
MAX_SLUG_LENGTH = 24


def decode_watchlist_metadata(display_description):
    """Decode the old `Z64:` cloud metadata format from a watchlist description.

    This is the one piece of `basket_utils.py` that the migration still
    needs, moved here when that module was deleted in Stage 3. It reads the
    old format; it writes nothing. Handles 'Z64:' compressed Base64, raw
    JSON `{"s":...}`, and the legacy `[BASKET_MODEL]` format.

    Returns a dict with `slug`, `weights`, `threshold`, and `snapshot`, or
    None when the description does not decode as basket metadata.
    """
    if not display_description:
        return None

    desc_str = display_description.strip()
    json_text = None

    if desc_str.startswith("Z64:"):
        try:
            b64_payload = desc_str[4:]
            compressed = base64.b64decode(b64_payload.encode("utf-8"))
            json_text = zlib.decompress(compressed).decode("utf-8")
        except Exception:
            return None
    elif desc_str.startswith("[BASKET_MODEL]"):
        match = re.search(r"\[BASKET_MODEL\]\s*(\{.*\})", desc_str)
        if not match:
            return None
        json_text = match.group(1)
    elif desc_str.startswith("{") and desc_str.endswith("}"):
        json_text = desc_str
    else:
        return None

    try:
        data = json.loads(json_text)
    except ValueError:
        return None

    if "s" not in data or "w" not in data:
        return data

    slug = data.get("s")
    raw_w = data.get("w", {})
    target_weights = {}
    snap_h = {}
    for sym, lst in raw_w.items():
        sym = sym.upper()
        if isinstance(lst, (list, tuple)) and len(lst) > 0:
            target_weights[sym] = float(lst[0])
            if len(lst) >= 3:
                snap_h[sym] = [float(lst[1]), float(lst[2])]
        elif isinstance(lst, (int, float)):
            target_weights[sym] = float(lst)

    ts_val = data.get("t")
    iso_ts = None
    if ts_val:
        iso_ts = datetime.datetime.fromtimestamp(
            ts_val, tz=datetime.timezone.utc).isoformat()

    snapshot = None
    if iso_ts and snap_h:
        snapshot = {"ts": iso_ts, "h": snap_h}

    return {
        "slug": slug,
        "weights": target_weights,
        "threshold": float(data.get("th", 5.0)),
        "snapshot": snapshot,
    }

EXIT_OK = 0
EXIT_VALIDATION = 1
EXIT_IO = 2

# An order of magnitude above the largest observed drift between a snapshot
# claim and the order that produced it (IPGP, 0.000106), and far below the
# smallest gap between two competing claims for the same symbol (LITE,
# 0.018). See the plan's "Attribution, resolved against the real data".
SHARE_TOLERANCE = 0.0005

BASKET_NAME_PREFIX = "Basket: "


class CliError(Exception):
    """An error the CLI reports as JSON on stderr."""

    def __init__(self, message, code, detail=None, exit_code=EXIT_VALIDATION):
        super(CliError, self).__init__(message)
        self.message = message
        self.code = code
        self.detail = detail or {}
        self.exit_code = exit_code


# --- reading the sources -----------------------------------------------------

def _unwrap_list(payload, *keys):
    """Peel a {"data": {"<key>": [...]}} envelope down to the bare list.

    Accepts the raw MCP response, a one-level envelope, or a bare list, so
    callers do not need to know which shape they were handed.
    """
    data = payload
    if isinstance(data, dict) and "data" in data:
        data = data["data"]
    if isinstance(data, dict):
        for key in keys:
            if key in data:
                return data[key] or []
        return []
    if isinstance(data, list):
        return data
    return []


def read_baskets(watchlists_response):
    """Return one dict per basket-bearing watchlist.

    Each dict carries `slug` (the metadata's own, possibly-truncated slug —
    NOT regenerated here), `name` (display name minus a leading "Basket: "),
    `weights`, `snapshot` (symbol -> [shares, avg_cost], or {} when the
    watchlist carries no positions yet), and `threshold`.

    A watchlist whose description does not decode as basket metadata is an
    ordinary list and is skipped.
    """
    baskets = []
    for watchlist in _unwrap_list(watchlists_response, "watchlists", "results"):
        if not isinstance(watchlist, dict):
            continue
        metadata = decode_watchlist_metadata(watchlist.get("display_description"))
        if not metadata:
            continue

        name = watchlist.get("display_name") or ""
        if name.startswith(BASKET_NAME_PREFIX):
            name = name[len(BASKET_NAME_PREFIX):]

        snapshot = metadata.get("snapshot") or {}
        snap_h = snapshot.get("h") or {} if isinstance(snapshot, dict) else {}

        baskets.append({
            "slug": metadata.get("slug"),
            "name": name,
            "weights": dict(metadata.get("weights") or {}),
            "snapshot": dict(snap_h),
            "threshold": metadata.get("threshold", 5.0),
        })
    return baskets


def normalize_basket_weights(weights):
    """Normalize a basket's weights and report whether anything changed."""
    normalized = normalize_weights(weights)
    changed = set(normalized) != set(weights) or any(
        normalized[symbol] != weights[symbol] for symbol in normalized
    )
    return normalized, changed


def read_legacy_theses(baskets_dir):
    """Return {slug: {symbol: thesis, "__description__": str}} from the old JSON files.

    Each legacy file's own `name` field is slugified with the SAME function
    (`basket_store.slugify`) that the migration uses to compute a basket's new
    slug from its watchlist display name. Because both sides derive their key
    from the same name string through the same function, the two agree
    without needing to reverse-engineer the old (possibly truncated)
    watchlist slug or parse the legacy filename.

    Returns {} when `baskets_dir` is falsy, missing, or holds no readable
    JSON files — the migration still runs, just with empty theses.
    """
    theses = {}
    if not baskets_dir:
        return theses
    directory = Path(baskets_dir)
    if not directory.exists():
        return theses

    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        name = data.get("name")
        if not name:
            continue

        entry = {"__description__": data.get("description", "") or ""}
        for holding in data.get("holdings") or []:
            if not isinstance(holding, dict):
                continue
            symbol = holding.get("symbol")
            if symbol:
                entry[symbol] = holding.get("thesis", "") or ""
        theses[slugify(name)] = entry
    return theses


# --- attribution --------------------------------------------------------------

def _order_shares(order):
    """Return the filled share count of an order, or 0.0.

    `cumulative_quantity` wins; `quantity` is the fallback. Robinhood returns
    `quantity: null` on a dollar-amount order and carries the real figure in
    `cumulative_quantity`.
    """
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

    `average_price` is the fill price; `price` is the limit/reference price
    and is deliberately never read. A quantity-weighted mean of `executions`
    is the only fallback.
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
    """Return the raw fill timestamp of an order, or None."""
    for raw in (order.get("last_transaction_at"), order.get("created_at")):
        if raw and parse_timestamp(raw) is not None:
            return raw
    for raw in (order.get("last_transaction_at"), order.get("created_at")):
        if raw:
            return raw
    return None


def _claimed_shares(snapshot_entry):
    """Return the share count a snapshot entry claims, or 0.0."""
    if isinstance(snapshot_entry, (list, tuple)) and snapshot_entry:
        try:
            return float(snapshot_entry[0])
        except (TypeError, ValueError):
            return 0.0
    if isinstance(snapshot_entry, dict):
        try:
            return float(snapshot_entry.get("shares", 0.0))
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def attribute_orders(baskets, orders):
    """Attribute each filled order to at most one basket.

    For each filled order, every basket whose weight set contains the
    order's symbol AND whose snapshot claims a non-zero share count for it
    is a candidate. Each candidate is scored by
    `abs(order_shares - claimed_shares)`. Every (order, basket) pair within
    `SHARE_TOLERANCE` is a contender; contenders are resolved globally,
    closest match first, so a closer match wins a contested claim and no
    order is assigned twice.

    A basket with no snapshot entry for a symbol claims nothing for it and
    can never win that symbol's orders, even if the symbol is in its
    weight set (see: semiconductor-etf-style, which holds NVDA and MU but
    carries no snapshot at all).

    Returns (assignments, unattributed): `assignments` maps order id to the
    winning basket's (metadata) slug; `unattributed` is the list of filled
    orders — the full order dict — that matched no candidate.
    """
    contenders = []
    filled_orders = []

    for order in orders:
        if not isinstance(order, dict) or order.get("state") != "filled":
            continue
        order_id = order.get("id")
        symbol = order.get("symbol")
        if not order_id or not symbol:
            continue
        shares = _order_shares(order)
        if shares <= 0:
            continue
        filled_orders.append(order)

        for basket in baskets:
            if symbol not in basket["weights"]:
                continue
            claimed = _claimed_shares(basket["snapshot"].get(symbol))
            if claimed <= 0:
                continue
            diff = abs(shares - claimed)
            if diff <= SHARE_TOLERANCE:
                contenders.append((diff, order_id, basket["slug"]))

    # Closest match first; ties break deterministically on order id then slug
    # so the result never depends on dict/list iteration order.
    contenders.sort(key=lambda c: (c[0], c[1], c[2]))

    assignments = {}
    for _diff, order_id, slug in contenders:
        if order_id in assignments:
            continue
        assignments[order_id] = slug

    unattributed = [o for o in filled_orders if o.get("id") not in assignments]
    return assignments, unattributed


# --- building events -----------------------------------------------------------

def _sort_order_ids_by_fill_time(order_ids, order_index):
    """Return order ids sorted by fill time, undated ids sorted last.

    Mirrors basket.py's `sort_order_ids`: the raw timestamp strings are not
    directly comparable (two to six fractional digits, an optional `Z`), so
    each one is parsed. An id with no usable time sorts last rather than
    landing in an arbitrary slot, and ties keep the caller's order.
    """
    decorated = []
    for position, order_id in enumerate(order_ids):
        order = order_index.get(order_id)
        stamp = parse_timestamp(_order_fill_time(order) if order else None)
        decorated.append((stamp is None, stamp or EPOCH, position, order_id))
    decorated.sort(key=lambda row: (row[0], row[1], row[2]))
    return [row[3] for row in decorated]


def resolve_slug(old_slug, display_name):
    """Return the slug a migrated basket should use.

    Prefers the slug already stored in the metadata — regenerating it from
    the display name unconditionally (the original behavior) changed five
    of the six live slugs, most of which were fine as-is. Regeneration
    happens only when:

    - the stored slug is missing/empty, or
    - the stored slug carries the unmistakable signature of the old
      format's truncation at `MAX_SLUG_LENGTH` (24) characters.

    Detecting a genuine truncation takes one wrinkle into account: the old
    (now-obsolete) encoder spelled "&" out as "and" rather than dropping it
    the way `basket_store.slugify` does, so a truncated slug like
    "optical-and-photonics-in" no longer reads as a literal prefix of
    today's regenerated "optical-photonics-index". Reconstructing the
    pre-truncation slug the old way (spelling "&" back out) recovers that
    relationship. A stored slug that merely happens to be 24 characters
    long but whose full reconstructed form was never longer than the cap —
    e.g. "storage-and-memory-index", which IS 24 characters whole — was
    never actually cut short, and is left alone.
    """
    old_slug = (old_slug or "").strip()
    regenerated = slugify(display_name)
    if not old_slug:
        return regenerated
    if len(old_slug) == MAX_SLUG_LENGTH:
        legacy_full = slugify(display_name.replace("&", " and "))
        if (len(legacy_full) > MAX_SLUG_LENGTH
                and legacy_full[:MAX_SLUG_LENGTH] == old_slug):
            return regenerated
    return old_slug


def _build_basket_plans(baskets, assignments, orders, theses, account):
    """Return one plan dict per basket: its events, and the fields the report needs.

    `build_events` and `migrate` both drive from this, so the slug rule, the
    weight normalization, and the event ordering are computed exactly once
    and can never drift between the two.
    """
    theses = theses or {}
    order_index = dict((o.get("id"), o) for o in orders if isinstance(o, dict) and o.get("id"))

    orders_by_old_slug = {}
    for order_id, old_slug in assignments.items():
        orders_by_old_slug.setdefault(old_slug, []).append(order_id)

    plans = []
    for basket in baskets:
        old_slug = basket["slug"]
        new_slug = resolve_slug(old_slug, basket["name"])
        normalized, changed = normalize_basket_weights(basket["weights"])
        # Legacy theses are keyed by slugify(name) (see read_legacy_theses),
        # which is not always this basket's final slug now that resolve_slug
        # can keep the stored one — fall back to the name-derived key so a
        # kept slug does not silently lose its carried-over thesis text.
        basket_theses = (theses.get(new_slug)
                         or theses.get(slugify(basket["name"]))
                         or {})

        events = [make_event(
            "basket_created", new_slug,
            name=basket["name"],
            description=basket_theses.get("__description__", ""),
            account_number=account,
            rebalance_threshold_pct=basket.get("threshold", 5.0),
        )]

        for symbol, weight in normalized.items():
            events.append(make_event(
                "holding_added", new_slug, symbol=symbol, weight=weight,
                thesis=basket_theses.get(symbol, "")))

        candidate_ids = orders_by_old_slug.get(old_slug, [])
        ordered_ids = _sort_order_ids_by_fill_time(candidate_ids, order_index)

        used_order_ids = []
        for order_id in ordered_ids:
            order = order_index.get(order_id)
            if order is None:
                continue
            symbol = order.get("symbol")
            if symbol not in normalized:
                continue
            shares = _order_shares(order)
            price = _order_price(order)
            if shares <= 0 or price <= 0:
                continue
            events.append(make_event(
                "buy", new_slug, symbol=symbol, shares=shares, price=price,
                amount=shares * price, order_id=order_id,
                ts=order.get("last_transaction_at")))
            used_order_ids.append(order_id)

        plans.append({
            "old_slug": old_slug,
            "new_slug": new_slug,
            "symbols": sorted(normalized),
            "orders": used_order_ids,
            "weights_changed": changed,
            "events": events,
        })
    return plans


def build_events(baskets, assignments, orders, theses, account):
    """Return the full ordered list of events for every basket.

    Per basket, in order: one `basket_created` (carrying `account`), one
    `holding_added` per symbol carrying its NORMALIZED weight, then one
    `buy` per attributed order in fill-time order.
    """
    events = []
    for plan in _build_basket_plans(baskets, assignments, orders, theses, account):
        events.extend(plan["events"])
    return events


# --- migration -----------------------------------------------------------------

def migrate(data_dir, watchlists, orders, legacy_dir, account, apply=False):
    """Read the sources, attribute orders, and write through the event log.

    `account` is the brokerage account number every migrated basket's
    `basket_created` event records. It is required: `record-fills`' account
    guard (basket.py) only fires when a basket's `account_number` is
    non-empty, so a basket migrated without one would silently accept fills
    from any account.

    Never writes when `apply` is False. When `apply` is True, skips any
    basket whose (new) slug already exists in the log, and skips any `buy`
    whose order id the log already carries — a second `--apply` adds
    nothing.

    Returns a report: `dry_run`, `baskets` (each with `old_slug`,
    `new_slug` — equal when the stored slug was kept unchanged — `symbols`,
    `orders`, `weights_changed`), `unattributed`, and `warnings`.
    """
    data_dir = Path(data_dir)
    baskets = read_baskets(watchlists)
    orders_list = [o for o in _unwrap_list(orders, "orders", "results") if isinstance(o, dict)]
    theses = read_legacy_theses(legacy_dir)

    assignments, unattributed = attribute_orders(baskets, orders_list)
    plans = _build_basket_plans(baskets, assignments, orders_list, theses, account)

    def plan_events(existing_slugs, existing_order_ids):
        """Return (basket_reports, new_events, warnings) against a given state.

        Shared by the dry-run path and the locked apply path below, so the
        skip rule — a basket whose slug already exists, or a buy whose
        order id already exists — is computed identically either way.
        """
        reports = []
        events = []
        notes = []
        for plan in plans:
            report_entry = {
                "old_slug": plan["old_slug"],
                "new_slug": plan["new_slug"],
                "symbols": plan["symbols"],
                "orders": plan["orders"],
                "weights_changed": plan["weights_changed"],
            }
            if plan["new_slug"] in existing_slugs:
                report_entry["already_migrated"] = True
                reports.append(report_entry)
                continue

            for event in plan["events"]:
                if event["type"] == "buy" and event.get("order_id") in existing_order_ids:
                    # The order was claimed by some other basket already on
                    # record. Emitting it again would only be ignored by
                    # replay's dedupe, so leave it out and say why.
                    notes.append(
                        "Order %s (basket %s) was already recorded elsewhere "
                        "and was skipped." % (event.get("order_id"), plan["new_slug"]))
                    continue
                events.append(event)
            reports.append(report_entry)
        return reports, events, notes

    log = EventLog(data_dir)

    if apply:
        # The read and the write happen under one exclusive lock, so a
        # concurrent writer cannot slip a basket or order in between the
        # check and the append. EventLog.append() takes the same lock
        # re-entrantly (the depth counter in EventLog.locked() supports
        # this), so this does not deadlock.
        with log.locked():
            existing = replay(log.read())
            basket_reports, new_events, warnings = plan_events(
                set(existing.baskets), set(existing.order_index))
            if new_events:
                log.append(new_events)
    else:
        # A dry run must leave no trace on disk. EventLog.read() takes only
        # a shared lock and never creates the file; log.locked() would open
        # the file in "a+" mode and create it as a side effect, so it is
        # never entered here.
        existing = replay(log.read())
        basket_reports, new_events, warnings = plan_events(
            set(existing.baskets), set(existing.order_index))

    return {
        "dry_run": not apply,
        "baskets": basket_reports,
        "unattributed": [
            {
                "order_id": order.get("id"),
                "symbol": order.get("symbol"),
                "shares": _order_shares(order),
                "fill_time": order.get("last_transaction_at"),
            }
            for order in unattributed
        ],
        "warnings": warnings,
    }


# --- command line ------------------------------------------------------------

class _ArgumentParser(argparse.ArgumentParser):
    """Report a parse failure as a CliError instead of exiting straight away.

    The base class's `error()` prints usage text and calls `sys.exit(2)`
    directly, which would bypass the JSON error envelope every other failure
    in this tool produces. Raising instead lets `main()` catch it alongside
    every other CliError and report it the same way, with the same exit
    code (1) that every other validation failure uses.
    """

    def error(self, message):
        raise CliError(message, "BAD_ARGS", {}, exit_code=EXIT_VALIDATION)


def build_parser():
    parser = _ArgumentParser(
        prog="migrate_v2.py",
        description="Move the user's live cloud baskets into the local event store")
    parser.add_argument("--watchlists-json", required=True,
                        help="The get_watchlists response, as JSON")
    parser.add_argument("--orders-json", required=True,
                        help="The get_equity_orders response, as JSON")
    parser.add_argument("--legacy-dir", default=None,
                        help="Directory of legacy data/baskets/*.json files")
    parser.add_argument("--account", required=True,
                        help="The brokerage account number every migrated "
                             "basket will carry (required — an empty "
                             "account_number disables record-fills' account "
                             "guard)")
    parser.add_argument("--data-dir", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--apply", action="store_true",
                        help="Write the migration. Without this, it is a dry run.")
    parser.add_argument("--format", choices=["json", "table"], default="json")
    return parser


def _load_json_arg(text, flag):
    try:
        return json.loads(text)
    except ValueError as error:
        raise CliError("The %s value is not valid JSON: %s" % (flag, error),
                       "BAD_JSON", {"flag": flag})


def print_table(report):
    """Print a human-readable summary of a migration report."""
    print("DRY RUN — no events were written. Pass --apply to write them."
          if report["dry_run"] else "APPLIED — events were written.")
    print()
    print("%-28s %-32s %8s %8s %s" % (
        "Old slug", "New slug", "Symbols", "Orders", "Weights changed"))
    print("-" * 90)
    for row in report["baskets"]:
        note = " (already migrated)" if row.get("already_migrated") else ""
        print("%-28s %-32s %8d %8d %s%s" % (
            row["old_slug"], row["new_slug"], len(row["symbols"]),
            len(row["orders"]), row["weights_changed"], note))

    if report["unattributed"]:
        print()
        print("Unattributed orders (no basket claimed them):")
        for order in report["unattributed"]:
            print("  %s  %s  %s shares  %s" % (
                order["order_id"], order["symbol"], order["shares"],
                order.get("fill_time") or ""))

    for warning in report.get("warnings", []):
        print("WARNING: %s" % warning)


def _emit_error(error):
    sys.stderr.write(json.dumps({
        "error": error.message, "code": error.code, "detail": error.detail,
    }) + "\n")
    return error.exit_code


def main(argv=None):
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except CliError as error:
        return _emit_error(error)

    data_dir = Path(args.data_dir) if args.data_dir else DEFAULT_DATA_DIR

    try:
        watchlists = _load_json_arg(args.watchlists_json, "--watchlists-json")
        orders = _load_json_arg(args.orders_json, "--orders-json")
        report = migrate(data_dir, watchlists, orders, args.legacy_dir,
                         args.account, apply=args.apply)
    except CliError as error:
        return _emit_error(error)
    except WeightError as error:
        return _emit_error(CliError(str(error), "BAD_WEIGHTS", {}))
    except OSError as error:
        sys.stderr.write(json.dumps({
            "error": str(error), "code": "IO_ERROR", "detail": {},
        }) + "\n")
        return EXIT_IO

    if report["dry_run"] and args.format != "table":
        # Prominent and on stderr, so it survives even when stdout is piped
        # into a JSON parser, and nobody mistakes a preview for a completed
        # migration. Table format already prints its own banner to stdout
        # (see print_table) — printing this one too would just repeat it.
        sys.stderr.write(
            "DRY RUN: no events were written. Re-run with --apply to write "
            "them.\n")

    if args.format == "table":
        print_table(report)
    else:
        print(json.dumps(report, indent=2))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
