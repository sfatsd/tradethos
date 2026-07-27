#!/usr/bin/env python3
"""Migrate local JSON baskets (data/baskets/*.json) to Robinhood Watchlist format.

Parses local JSON basket files and extracts target weights and baseline position snapshots,
then formats the ultra-compact display_description and Watchlist payload.

Usage:
    python3 skills/basket-manager/scripts/migrate_to_watchlists.py
"""

import json
import sys
from pathlib import Path

# Add basket-manager scripts directory to sys.path
scripts_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(scripts_dir))

from basket_utils import encode_watchlist_metadata


def find_baskets_dir() -> Path:
    curr = Path(__file__).resolve().parent
    while curr != curr.parent:
        target = curr / "data" / "baskets"
        if target.exists():
            return target
        curr = curr.parent
    return Path(__file__).resolve().parents[3] / "data" / "baskets"


def load_basket_data(file_path: Path) -> dict:
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    name = data.get("name", file_path.stem.replace("-", " ").title())
    slug = data.get("slug", file_path.stem)
    threshold = data.get("rebalance_threshold_pct", 5.0)

    target_weights = {}
    snap_h = {}

    holdings = data.get("holdings", [])
    for h in holdings:
        sym = h.get("symbol", "").upper()
        if not sym:
            continue
        weight = float(h.get("target_weight_pct", 0.0))
        target_weights[sym] = weight

        pos = h.get("position")
        if pos and isinstance(pos, dict):
            shares = float(pos.get("shares", 0.0))
            avg_cost = float(pos.get("avg_cost", 0.0))
            if shares > 0:
                snap_h[sym] = [shares, avg_cost]

    snapshot = None
    if snap_h:
        ts_str = data.get("updated_at") or data.get("created_at") or "2026-07-24T00:00:00Z"
        snapshot = {"ts": ts_str, "h": snap_h}

    encoded_desc = encode_watchlist_metadata(
        slug=slug,
        target_weights=target_weights,
        rebalance_threshold_pct=threshold,
        snapshot=snapshot,
    )

    symbols = list(target_weights.keys())

    return {
        "name": name,
        "slug": slug,
        "symbols": symbols,
        "description": encoded_desc,
        "create_watchlist_arg": {
            "display_name": f"Basket: {name}",
            "display_description": encoded_desc,
            "icon_emoji": "🧺",
        },
        "add_to_watchlist_arg": {
            "symbols": symbols,
        },
    }


def main():
    baskets_dir = find_baskets_dir()
    if not baskets_dir.exists():
        print(f"No baskets directory found at {baskets_dir}")
        return

    basket_files = sorted(list(baskets_dir.glob("*.json")))
    basket_files = [f for f in basket_files if f.name != ".gitkeep"]

    if not basket_files:
        print(f"No local JSON basket files found in {baskets_dir}")
        return

    print(f"Found {len(basket_files)} local basket file(s) for migration:\n")

    for file_path in basket_files:
        try:
            res = load_basket_data(file_path)
            print("=" * 65)
            print(f"Basket Name: {res['name']} ({file_path.name})")
            print(f"Watchlist Name: Basket: {res['name']}")
            print(f"Symbols: {res['symbols']}")
            print(f"Ultra-Compact Description ({len(res['description'])} chars): {res['description']}")
            print("\n1. create_watchlist call:")
            print(json.dumps(res["create_watchlist_arg"], indent=2))
            print("\n2. add_to_watchlist call:")
            print(json.dumps(res["add_to_watchlist_arg"], indent=2))
            print("=" * 65 + "\n")

        except Exception as e:
            print(f"Error processing {file_path}: {e}")


if __name__ == "__main__":
    main()
