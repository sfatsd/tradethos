#!/usr/bin/env python3
"""Calculate weight drift for basket holdings against target allocations.

Compares the actual weight (by current market value) to the target weight
and flags a holding that exceeds the drift threshold. It reads baskets from
the local event-log store by replaying the log itself. It writes nothing.

Usage:
    python3 skills/basket-manager/scripts/calc_drift.py --slug storage-leaders \\
      --prices '{"WDC":60.00,"STX":92.00,"MU":98.50}'

    python3 skills/basket-manager/scripts/calc_drift.py --all \\
      --prices '{"WDC":60.00,...}' --threshold 3.0
"""

import argparse
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from basket_events import EventLog, corrupt_line_warnings
from basket_store import replay, snapshot_dict

DEFAULT_DATA_DIR = Path.home() / ".tradethos"


def find_config_path():
    """Locate config.json by walking up from this file."""
    curr = Path(__file__).resolve().parent
    while curr != curr.parent:
        target = curr / "config.json"
        if target.exists():
            return target
        curr = curr.parent
    return Path(__file__).resolve().parents[3] / "config.json"


CONFIG_PATH = find_config_path()

DEFAULT_REBALANCE_THRESHOLD = 5.0
DEFAULT_ON_TARGET_THRESHOLD = 2.0


def load_config():
    """Load project config for threshold defaults."""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


def load_baskets(data_dir):
    """Replay the event log. Return ({slug: snapshot_dict}, corrupt_lines).

    A torn line - what a crash mid-flush leaves behind - must not make this
    script silently under-report, the same way `basket.py show` will not:
    `corrupt_lines` rides along so the caller can warn about it instead of
    just returning numbers computed from an incomplete log.
    """
    events, corrupt = EventLog(data_dir).read_with_corruption()
    result = replay(events, corrupt)
    baskets = dict((slug, snapshot_dict(b)) for slug, b in result.baskets.items())
    return baskets, corrupt


def classify_drift(drift_abs, on_target_threshold, rebalance_threshold):
    """Classify a drift level: on_target, minor_drift, or significant_drift."""
    if drift_abs <= on_target_threshold:
        return "on_target"
    elif drift_abs <= rebalance_threshold:
        return "minor_drift"
    else:
        return "significant_drift"


def calc_basket_drift(data, prices, threshold, on_target):
    """Calculate the drift of every holding in one basket."""
    holdings = data.get("holdings", [])

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
            entry["note"] = "No position. The actual weight is 0%."
            entry["status"] = "significant_drift" if hv["target_weight_pct"] > 0 else "on_target"
        elif not hv["has_price"]:
            entry["note"] = "No price given for %s" % hv["symbol"]

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
    config = load_config()
    rebalance_cfg = config.get("rebalancing", {})
    default_threshold = rebalance_cfg.get("default_threshold_pct", DEFAULT_REBALANCE_THRESHOLD)
    default_on_target = rebalance_cfg.get("on_target_threshold_pct", DEFAULT_ON_TARGET_THRESHOLD)

    parser = argparse.ArgumentParser(description="Calculate basket weight drift")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--slug", help="Basket slug (e.g. storage-leaders)")
    group.add_argument("--all", action="store_true", help="Process every basket")
    parser.add_argument("--data-dir", default=None,
                        help="The event-log directory. Default: ~/.tradethos")
    parser.add_argument("--prices", required=True,
                        help="JSON object of symbol:price pairs")
    parser.add_argument("--threshold", type=float, default=None,
                        help="Drift threshold percent to flag. Overrides the basket's own "
                             "rebalance_threshold_pct (default: per-basket value, else %s)"
                             % default_threshold)
    parser.add_argument("--format", choices=["json", "table"], default="json",
                        help="Output format (default: json)")
    args = parser.parse_args()

    try:
        prices = json.loads(args.prices)
    except ValueError as error:
        print("Error: Invalid JSON in --prices: %s" % error, file=sys.stderr)
        sys.exit(1)

    def resolve_threshold(basket):
        """An explicit --threshold outranks the basket's own field, which outranks config.json."""
        if args.threshold is not None:
            return args.threshold
        return basket.get("rebalance_threshold_pct", default_threshold)

    data_dir = Path(args.data_dir) if args.data_dir else DEFAULT_DATA_DIR
    baskets, corrupt = load_baskets(data_dir)
    chosen = select_baskets(baskets, args.slug, args.all)
    results = [calc_basket_drift(data, prices, resolve_threshold(data), default_on_target)
               for data in chosen]
    warnings = corrupt_line_warnings(corrupt) if corrupt else []

    if args.format == "json":
        output = results[0] if len(results) == 1 else {"baskets": results}
        if warnings:
            output["warnings"] = warnings
        print(json.dumps(output, indent=2))
    else:
        for drift in results:
            print("\n%s" % ("=" * 70))
            print("  %s  (threshold: +/-%s%%)" % (drift["basket"], drift["threshold_pct"]))
            print("  Total Value: $%s" % format(drift["total_value"], ",.2f"))
            print("=" * 70)

            status_marks = {"on_target": "OK", "minor_drift": "WATCH",
                            "significant_drift": "REBALANCE"}
            header = "%-8s | %8s | %8s | %8s | %-20s" % (
                "Symbol", "Target", "Actual", "Drift", "Status")
            print(header)
            print("-" * len(header))
            for h in drift["holdings"]:
                mark = status_marks.get(h["status"], "?")
                drift_str = "%s%.2f%%" % ("+" if h["drift_pct"] >= 0 else "", h["drift_pct"])
                print("%-8s | %7.2f%% | %7.2f%% | %8s | %s %s" % (
                    h["symbol"], h["target_weight_pct"], h["actual_weight_pct"],
                    drift_str, mark, h["status"]))

            if drift["rebalance_needed"]:
                print("\nRebalance suggested for: %s" % ", ".join(drift["flagged"]))
            else:
                print("\nAll holdings are within +/-%s%% drift tolerance"
                      % drift["threshold_pct"])

        for warning in warnings:
            print("WARNING: %s" % warning)


if __name__ == "__main__":
    main()
