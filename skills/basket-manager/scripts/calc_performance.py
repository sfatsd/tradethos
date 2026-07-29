#!/usr/bin/env python3
"""Calculate P&L for basket holdings using provided live prices.

Prices are passed as a JSON object via --prices, so the script stays a pure
data processor. It reads baskets from the local event-log store by replaying
the log itself. It opens no snapshot file, and it writes nothing.

`basket.py show --prices` already reports the current value and the profit
or loss for each holding. This script adds the profit-and-loss percentage
and a table across every basket in one call.

Usage:
    python3 skills/basket-manager/scripts/calc_performance.py --slug storage-leaders \\
      --prices '{"WDC":60.00,"STX":92.00,"MU":98.50}'

    python3 skills/basket-manager/scripts/calc_performance.py --all \\
      --prices '{"WDC":60.00,"STX":92.00,...}'
"""

import argparse
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from basket_events import EventLog
from basket_store import replay, snapshot_dict

DEFAULT_DATA_DIR = Path.home() / ".tradethos"


def load_baskets(data_dir):
    """Replay the event log. Return {slug: snapshot_dict}."""
    events, corrupt = EventLog(data_dir).read_with_corruption()
    result = replay(events, corrupt)
    return dict((slug, snapshot_dict(b)) for slug, b in result.baskets.items())


def calc_holding_perf(holding, prices):
    """Calculate the performance of a single holding."""
    symbol = holding["symbol"]
    position = holding.get("position")
    current_price = prices.get(symbol)

    result = {
        "symbol": symbol,
        "target_weight_pct": holding.get("target_weight_pct", 0),
        "has_position": position is not None,
    }

    if position is None or current_price is None:
        result.update({
            "shares": 0,
            "avg_cost": 0,
            "total_invested": 0,
            "current_price": current_price,
            "current_value": 0,
            "pnl": 0,
            "pnl_pct": 0,
        })
        if current_price is None:
            result["error"] = "No price provided for %s" % symbol
        return result

    shares = position.get("shares", 0)
    avg_cost = position.get("avg_cost", 0)
    total_invested = position.get("total_invested", 0)
    current_value = round(shares * current_price, 2)
    pnl = round(current_value - total_invested, 2)
    pnl_pct = round((pnl / total_invested) * 100, 2) if total_invested > 0 else 0

    result.update({
        "shares": shares,
        "avg_cost": avg_cost,
        "total_invested": total_invested,
        "current_price": current_price,
        "current_value": current_value,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
    })
    return result


def calc_basket_perf(data, prices):
    """Calculate the performance of one whole basket."""
    holdings_perf = [calc_holding_perf(h, prices) for h in data.get("holdings", [])]

    total_invested = sum(h["total_invested"] for h in holdings_perf)
    total_value = sum(h["current_value"] for h in holdings_perf)
    total_pnl = round(total_value - total_invested, 2)
    total_pnl_pct = round((total_pnl / total_invested) * 100, 2) if total_invested > 0 else 0

    missing = [h["symbol"] for h in holdings_perf if "error" in h]

    result = {
        "basket": data.get("name", "Unknown"),
        "total_invested": total_invested,
        "current_value": round(total_value, 2),
        "total_pnl": total_pnl,
        "total_pnl_pct": total_pnl_pct,
        "holdings": holdings_perf,
    }
    if missing:
        result["missing_prices"] = missing
    return result


def select_baskets(baskets, slug, all_baskets):
    if slug:
        if slug not in baskets:
            print("Error: Basket %r not found. Available slugs: %s"
                  % (slug, ", ".join(sorted(baskets)) or "(none)"), file=sys.stderr)
            sys.exit(1)
        return [baskets[slug]]
    if all_baskets:
        return [baskets[s] for s in sorted(baskets)]
    print("Error: Specify --slug <slug> or --all", file=sys.stderr)
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Calculate basket P&L with live prices")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--slug", help="Basket slug (e.g. storage-leaders)")
    group.add_argument("--all", action="store_true", help="Process every basket")
    parser.add_argument("--data-dir", default=None,
                        help="The event-log directory. Default: ~/.tradethos")
    parser.add_argument("--prices", required=True,
                        help="JSON object of symbol:price pairs")
    parser.add_argument("--format", choices=["json", "table"], default="json",
                        help="Output format (default: json)")
    args = parser.parse_args()

    try:
        prices = json.loads(args.prices)
    except ValueError as error:
        print("Error: Invalid JSON in --prices: %s" % error, file=sys.stderr)
        sys.exit(1)

    data_dir = Path(args.data_dir) if args.data_dir else DEFAULT_DATA_DIR
    baskets = load_baskets(data_dir)
    chosen = select_baskets(baskets, args.slug, args.all)
    results = [calc_basket_perf(data, prices) for data in chosen]

    if args.format == "json":
        output = results[0] if len(results) == 1 else {"baskets": results}
        print(json.dumps(output, indent=2))
    else:
        for perf in results:
            print("\n%s" % ("=" * 60))
            print("  %s" % perf["basket"])
            print("=" * 60)
            header = "%-8s | %10s | %10s | %10s | %10s | %12s | %8s" % (
                "Symbol", "Shares", "Avg Cost", "Price", "Value", "P&L", "P&L %")
            print(header)
            print("-" * len(header))
            for h in perf["holdings"]:
                if not h["has_position"]:
                    print("%-8s | %10s | %10s | %10.2f | %10s | %12s | %8s" % (
                        h["symbol"], "-", "-", h.get("current_price") or 0, "-", "-", "-"))
                else:
                    pnl_str = "%s%.2f" % ("+" if h["pnl"] >= 0 else "", h["pnl"])
                    pct_str = "%s%.2f%%" % ("+" if h["pnl_pct"] >= 0 else "", h["pnl_pct"])
                    print("%-8s | %10.5f | %10.2f | %10.2f | %10.2f | %12s | %8s" % (
                        h["symbol"], h["shares"], h["avg_cost"], h["current_price"],
                        h["current_value"], pnl_str, pct_str))
            print("-" * len(header))
            total_pnl_str = "%s%.2f" % ("+" if perf["total_pnl"] >= 0 else "", perf["total_pnl"])
            total_pct_str = "%s%.2f%%" % (
                "+" if perf["total_pnl_pct"] >= 0 else "", perf["total_pnl_pct"])
            print("%-8s | %10s | %10s | %10s | %10.2f | %12s | %8s" % (
                "Total", "", "", "", perf["current_value"], total_pnl_str, total_pct_str))


if __name__ == "__main__":
    main()
