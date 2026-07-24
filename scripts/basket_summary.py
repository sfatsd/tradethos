#!/usr/bin/env python3
"""Quick overview of all baskets: name, holdings count, invested amount, weight status.

Usage:
    python3 scripts/basket_summary.py                # Table format
    python3 scripts/basket_summary.py --format json   # JSON output
"""

import argparse
import json
import sys
from pathlib import Path

BASKETS_DIR = Path(__file__).resolve().parent.parent / "data" / "baskets"


def load_basket(filepath: Path) -> dict:
    """Load and parse a basket JSON file."""
    with open(filepath) as f:
        return json.load(f)


def summarize_basket(data: dict) -> dict:
    """Extract summary metrics from a basket."""
    holdings = data.get("holdings", [])
    total_weight = sum(h.get("target_weight_pct", 0) for h in holdings)
    positioned = sum(1 for h in holdings if h.get("position") is not None)
    total_invested = data.get("total_invested", 0)

    return {
        "name": data.get("name", "Unknown"),
        "description": data.get("description", ""),
        "holdings_count": len(holdings),
        "total_weight_pct": round(total_weight, 2),
        "weight_status": "ok" if abs(total_weight - 100.0) < 0.01 else (
            "over" if total_weight > 100 else "under"
        ),
        "positioned_count": positioned,
        "total_invested": total_invested,
        "symbols": [h["symbol"] for h in holdings],
    }


def main():
    parser = argparse.ArgumentParser(description="Summary of all basket files")
    parser.add_argument("--format", choices=["table", "json"], default="table",
                        help="Output format (default: table)")
    args = parser.parse_args()

    basket_files = sorted(p for p in BASKETS_DIR.glob("*.json") if p.name != ".gitkeep")
    if not basket_files:
        print("No basket files found.", file=sys.stderr)
        sys.exit(1)

    summaries = []
    for filepath in basket_files:
        data = load_basket(filepath)
        summary = summarize_basket(data)
        summary["slug"] = filepath.stem
        summaries.append(summary)

    if args.format == "json":
        print(json.dumps({"baskets": summaries}, indent=2))
    else:
        # Table format
        header = f"{'Basket':<30} | {'Holdings':>8} | {'Invested':>10} | {'Weights':>8} | {'Positioned':>10}"
        separator = "-" * len(header)
        print(header)
        print(separator)
        for s in summaries:
            invested_str = f"${s['total_invested']:,.2f}"
            weight_str = f"{s['total_weight_pct']:.1f}%"
            pos_str = f"{s['positioned_count']}/{s['holdings_count']}"
            print(f"{s['name']:<30} | {s['holdings_count']:>8} | {invested_str:>10} | {weight_str:>8} | {pos_str:>10}")


if __name__ == "__main__":
    main()
