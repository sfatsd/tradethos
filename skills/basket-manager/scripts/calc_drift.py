#!/usr/bin/env python3
"""Calculate weight drift for basket holdings against target allocations.

Compares actual weights (based on current market value) to target weights
and flags holdings that exceed the configured drift threshold.

Usage:
    python3 skills/basket-manager/scripts/calc_drift.py --basket storage-and-memory-index \
      --prices '{"WDC":560.00,"STX":920.00,"MU":985.19,"SNDK":1612.08,"MRVL":209.92,"LITE":832.18}'

    python3 skills/basket-manager/scripts/calc_drift.py --all \
      --prices '{"WDC":560.00,...}' --threshold 3.0
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


def find_config_path() -> Path:
    """Dynamically locate config.json by traversing upwards."""
    curr = Path(__file__).resolve().parent
    while curr != curr.parent:
        target = curr / "config.json"
        if target.exists():
            return target
        curr = curr.parent
    return Path(__file__).resolve().parents[3] / "config.json"


BASKETS_DIR = find_baskets_dir()
CONFIG_PATH = find_config_path()

DEFAULT_REBALANCE_THRESHOLD = 5.0
DEFAULT_ON_TARGET_THRESHOLD = 2.0


def load_config() -> dict:
    """Load project config for threshold defaults."""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


def load_basket(filepath: Path) -> dict:
    with open(filepath) as f:
        return json.load(f)


def classify_drift(drift_abs: float, on_target_threshold: float, rebalance_threshold: float) -> str:
    """Classify drift level: on_target, minor_drift, or significant_drift."""
    if drift_abs <= on_target_threshold:
        return "on_target"
    elif drift_abs <= rebalance_threshold:
        return "minor_drift"
    else:
        return "significant_drift"


def calc_basket_drift(data: dict, prices: dict, threshold: float, on_target: float) -> dict:
    """Calculate drift for all holdings in a basket."""
    holdings = data.get("holdings", [])

    # Calculate current value per holding
    holding_values = []
    for h in holdings:
        symbol = h["symbol"]
        position = h.get("position")
        current_price = prices.get(symbol)

        if position is not None and current_price is not None:
            shares = position.get("shares", 0)
            value = shares * current_price
        else:
            value = 0.0

        holding_values.append({
            "symbol": symbol,
            "target_weight_pct": h.get("target_weight_pct", 0),
            "current_value": value,
            "has_position": position is not None,
            "has_price": current_price is not None,
        })

    total_value = sum(hv["current_value"] for hv in holding_values)

    # Calculate actual weights and drift
    drift_results = []
    flagged = []
    for hv in holding_values:
        actual_weight = round((hv["current_value"] / total_value) * 100, 2) if total_value > 0 else 0
        drift = round(actual_weight - hv["target_weight_pct"], 2)
        drift_abs = abs(drift)
        status = classify_drift(drift_abs, on_target, threshold)

        entry = {
            "symbol": hv["symbol"],
            "target_weight_pct": hv["target_weight_pct"],
            "actual_weight_pct": actual_weight,
            "drift_pct": drift,
            "status": status,
        }

        if not hv["has_position"]:
            entry["note"] = "No position — actual weight is 0%"
            entry["status"] = "significant_drift" if hv["target_weight_pct"] > 0 else "on_target"
        elif not hv["has_price"]:
            entry["note"] = f"No price provided for {hv['symbol']}"

        drift_results.append(entry)
        if entry["status"] == "significant_drift":
            flagged.append(hv["symbol"])

    return {
        "basket": data.get("name", "Unknown"),
        "threshold_pct": threshold,
        "on_target_threshold_pct": on_target,
        "total_value": round(total_value, 2),
        "holdings": drift_results,
        "rebalance_needed": len(flagged) > 0,
        "flagged": flagged,
    }


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
    config = load_config()
    rebalance_cfg = config.get("rebalancing", {})
    default_threshold = rebalance_cfg.get("default_threshold_pct", DEFAULT_REBALANCE_THRESHOLD)
    default_on_target = rebalance_cfg.get("on_target_threshold_pct", DEFAULT_ON_TARGET_THRESHOLD)

    parser = argparse.ArgumentParser(description="Calculate basket weight drift")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--basket", help="Basket slug (e.g. storage-and-memory-index)")
    group.add_argument("--all", action="store_true", help="Process all baskets")
    parser.add_argument("--prices", required=True,
                        help="JSON object of symbol:price pairs")
    parser.add_argument("--threshold", type=float, default=default_threshold,
                        help=f"Drift threshold %% to flag (default: {default_threshold})")
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
        # Use per-basket threshold if set, otherwise CLI arg / config default
        basket_threshold = data.get("rebalance_threshold_pct", args.threshold)
        drift = calc_basket_drift(data, prices, basket_threshold, default_on_target)
        results.append(drift)

    if args.format == "json":
        output = results[0] if len(results) == 1 else {"baskets": results}
        print(json.dumps(output, indent=2))
    else:
        for drift in results:
            print(f"\n{'=' * 70}")
            print(f"  {drift['basket']}  (threshold: ±{drift['threshold_pct']}%)")
            print(f"  Total Value: ${drift['total_value']:,.2f}")
            print(f"{'=' * 70}")

            status_icons = {"on_target": "✅", "minor_drift": "⚠️", "significant_drift": "🔴"}
            header = f"{'Symbol':<8} | {'Target':>8} | {'Actual':>8} | {'Drift':>8} | {'Status':<20}"
            print(header)
            print("-" * len(header))
            for h in drift["holdings"]:
                icon = status_icons.get(h["status"], "?")
                drift_str = f"{'+' if h['drift_pct'] >= 0 else ''}{h['drift_pct']:.2f}%"
                print(f"{h['symbol']:<8} | {h['target_weight_pct']:>7.2f}% | {h['actual_weight_pct']:>7.2f}% | {drift_str:>8} | {icon} {h['status']}")

            if drift["rebalance_needed"]:
                print(f"\n⚠️  Rebalance suggested for: {', '.join(drift['flagged'])}")
            else:
                print(f"\n✅ All holdings within ±{drift['threshold_pct']}% drift tolerance")


if __name__ == "__main__":
    main()
