#!/usr/bin/env python3
"""Calculate P&L for basket holdings using provided live prices.

Prices are passed as a JSON object via --prices so the script stays a pure
data processor with no external API dependencies.

Usage:
    python3 skills/basket-manager/scripts/calc_performance.py --basket storage-and-memory-index \
      --prices '{"WDC":560.00,"STX":920.00,"MU":985.19,"SNDK":1612.08,"MRVL":209.92,"LITE":832.18}'

    python3 skills/basket-manager/scripts/calc_performance.py --all \
      --prices '{"WDC":560.00,"STX":920.00,...}'
"""

import argparse
import json
import sys
from pathlib import Path


def find_baskets_dir() -> Path:
    """Dynamically locate the data/baskets directory by traversing upwards."""
    curr = Path(__file__).resolve().parent
    while curr != curr.parent:
        target = curr / "data" / "baskets"
        if target.exists():
            return target
        curr = curr.parent
    return Path(__file__).resolve().parents[3] / "data" / "baskets"


BASKETS_DIR = find_baskets_dir()


def load_basket(filepath: Path) -> dict:
    with open(filepath) as f:
        return json.load(f)


def calc_holding_perf(holding: dict, prices: dict) -> dict:
    """Calculate performance for a single holding."""
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
            result["error"] = f"No price provided for {symbol}"
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


def calc_basket_perf(data: dict, prices: dict) -> dict:
    """Calculate performance for an entire basket."""
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


def get_basket_files(basket_slug: str = None, all_baskets: bool = False) -> list[Path]:
    if basket_slug:
        target = BASKETS_DIR / f"{basket_slug}.json"
        if not target.exists():
            print(f"Error: Basket '{basket_slug}' not found at {target}", file=sys.stderr)
            sys.exit(1)
        return [target]
    if all_baskets:
        return sorted(p for p in BASKETS_DIR.glob("*.json") if p.name != ".gitkeep")
    print("Error: Specify --basket <slug> or --all", file=sys.stderr)
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Calculate basket P&L with live prices")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--basket", help="Basket slug (e.g. storage-and-memory-index)")
    group.add_argument("--all", action="store_true", help="Process all baskets")
    parser.add_argument("--prices", required=True,
                        help="JSON object of symbol:price pairs")
    parser.add_argument("--format", choices=["json", "table"], default="json",
                        help="Output format (default: json)")
    args = parser.parse_args()

    try:
        prices = json.loads(args.prices)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in --prices: {e}", file=sys.stderr)
        sys.exit(1)

    basket_files = get_basket_files(args.basket, args.all)
    results = []

    for filepath in basket_files:
        data = load_basket(filepath)
        perf = calc_basket_perf(data, prices)
        results.append(perf)

    if args.format == "json":
        output = results[0] if len(results) == 1 else {"baskets": results}
        print(json.dumps(output, indent=2))
    else:
        for perf in results:
            print(f"\n{'=' * 60}")
            print(f"  {perf['basket']}")
            print(f"{'=' * 60}")
            header = f"{'Symbol':<8} | {'Shares':>10} | {'Avg Cost':>10} | {'Price':>10} | {'Value':>10} | {'P&L':>12} | {'P&L %':>8}"
            print(header)
            print("-" * len(header))
            for h in perf["holdings"]:
                if not h["has_position"]:
                    print(f"{h['symbol']:<8} | {'—':>10} | {'—':>10} | {h.get('current_price', 0):>10.2f} | {'—':>10} | {'—':>12} | {'—':>8}")
                else:
                    pnl_str = f"{'+'if h['pnl']>=0 else ''}{h['pnl']:,.2f}"
                    pct_str = f"{'+'if h['pnl_pct']>=0 else ''}{h['pnl_pct']:.2f}%"
                    print(f"{h['symbol']:<8} | {h['shares']:>10.5f} | {h['avg_cost']:>10.2f} | {h['current_price']:>10.2f} | {h['current_value']:>10.2f} | {pnl_str:>12} | {pct_str:>8}")
            print("-" * len(header))
            total_pnl_str = f"{'+'if perf['total_pnl']>=0 else ''}{perf['total_pnl']:,.2f}"
            print(f"{'Total':<8} | {'':>10} | {'':>10} | {'':>10} | {perf['current_value']:>10.2f} | {total_pnl_str:>12} | {'+'if perf['total_pnl_pct']>=0 else ''}{perf['total_pnl_pct']:.2f}%")


if __name__ == "__main__":
    main()
