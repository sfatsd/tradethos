#!/usr/bin/env python3
"""Extract unique stock symbols from basket JSON files.

Usage:
    python3 skills/basket-manager/scripts/list_symbols.py                          # All baskets, table format
    python3 skills/basket-manager/scripts/list_symbols.py --basket storage-and-memory-index  # Single basket
    python3 skills/basket-manager/scripts/list_symbols.py --format json             # JSON output
"""

import argparse
import json
import sys
from pathlib import Path

# Add basket-manager scripts directory to sys.path
scripts_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(scripts_dir))

from basket_utils import (
    MAX_SLUG_LENGTH,
    iter_watchlist_baskets,
    parse_watchlists_json,
    watchlist_to_basket_dict,
)


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
    """Load and parse a basket JSON file."""
    with open(filepath) as f:
        return json.load(f)


def get_basket_files(basket_slug: str = None) -> list[Path]:
    """Return basket file paths, optionally filtered to a single basket."""
    if basket_slug:
        target = BASKETS_DIR / f"{basket_slug}.json"
        if not target.exists():
            print(f"Error: Basket '{basket_slug}' not found at {target}", file=sys.stderr)
            sys.exit(1)
        return [target]
    return sorted(p for p in BASKETS_DIR.glob("*.json") if p.name != ".gitkeep")


def main():
    parser = argparse.ArgumentParser(description="Extract symbols from basket files or watchlists")
    parser.add_argument("--basket", help="Basket slug (e.g. storage-and-memory-index)")
    parser.add_argument("--watchlists-json", help="JSON array of watchlists returned by get_watchlists")
    parser.add_argument("--format", choices=["table", "json"], default="table",
                        help="Output format (default: table)")
    args = parser.parse_args()

    baskets_data = {}
    all_symbols = set()

    if args.watchlists_json:
        try:
            wl_list = parse_watchlists_json(args.watchlists_json)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Error parsing --watchlists-json: {e}", file=sys.stderr)
            sys.exit(1)

        for name, desc, _metadata in iter_watchlist_baskets(wl_list):
            b_dict = watchlist_to_basket_dict(name, desc)
            slug = b_dict["slug"]
            # Stored slugs are truncated to MAX_SLUG_LENGTH, so compare the
            # user's slug truncated the same way or the lookup never matches.
            if args.basket and slug != args.basket[:MAX_SLUG_LENGTH]:
                continue
            symbols = [h["symbol"] for h in b_dict["holdings"]]
            baskets_data[slug] = {
                "name": b_dict["name"],
                "symbols": symbols,
            }
            all_symbols.update(symbols)
    else:
        basket_files = get_basket_files(args.basket)
        if not basket_files:
            print("No basket files found.", file=sys.stderr)
            sys.exit(1)

        for filepath in basket_files:
            data = load_basket(filepath)
            slug = filepath.stem
            symbols = [h["symbol"] for h in data.get("holdings", [])]
            baskets_data[slug] = {
                "name": data.get("name", slug),
                "symbols": symbols,
            }
            all_symbols.update(symbols)

    if args.format == "json":
        output = {
            "total_baskets": len(baskets_data),
            "unique_symbols": sorted(all_symbols),
            "unique_count": len(all_symbols),
            "baskets": {
                slug: info["symbols"] for slug, info in baskets_data.items()
            },
        }
        print(json.dumps(output, indent=2))

    else:  # table
        print(f"Baskets: {len(baskets_data)}")
        print(f"Unique symbols: {len(all_symbols)}")
        print()
        for slug, info in baskets_data.items():
            print(f"{info['name']}: {', '.join(info['symbols'])}")


if __name__ == "__main__":
    main()
