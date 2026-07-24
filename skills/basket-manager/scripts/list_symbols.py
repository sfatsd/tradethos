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
    parser = argparse.ArgumentParser(description="Extract symbols from basket files")
    parser.add_argument("--basket", help="Basket slug (e.g. storage-and-memory-index)")
    parser.add_argument("--format", choices=["table", "json"], default="table",
                        help="Output format (default: table)")
    args = parser.parse_args()

    basket_files = get_basket_files(args.basket)
    if not basket_files:
        print("No basket files found.", file=sys.stderr)
        sys.exit(1)

    baskets_data = {}
    all_symbols = set()

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
