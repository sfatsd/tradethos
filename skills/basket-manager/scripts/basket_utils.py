#!/usr/bin/env python3
"""Utility module for Tradethos Cloud-Native Basket Management.

Provides helper functions for:
1. Encoding/decoding basket metadata into ultra-compact Robinhood Watchlist descriptions.
2. Generating deterministic UUID v5 ref_id keys for basket order idempotency.
3. Reconstructing basket holdings & average cost from filled orders + baseline snapshots.
4. Performing in-place baseline updates after trade fills so metadata size never grows.
5. Converting Watchlist data into standard Basket dictionaries for performance/drift scripts.
"""

import datetime
import json
import re
import time
import uuid
from typing import Any, Dict, List, Optional

# Fixed namespace UUID for Tradethos basket order tagging
TRADETHOS_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def generate_basket_order_ref_id(basket_slug: str, symbol: str, timestamp: str, order_idx: int = 0) -> str:
    """Generate a deterministic UUID v5 ref_id for order placement idempotency.

    Args:
        basket_slug: Kebab-case identifier of the basket (e.g. 'storage-leaders').
        symbol: Ticker symbol (e.g. 'WDC').
        timestamp: ISO format or unique string timestamp of order creation.
        order_idx: Sequential index if multiple orders are generated in a single batch.

    Returns:
        UUID string to pass as ref_id in place_equity_order.
    """
    name_str = f"tradethos:basket:{basket_slug}:symbol:{symbol}:ts:{timestamp}:idx:{order_idx}"
    return str(uuid.uuid5(TRADETHOS_NAMESPACE, name_str))


def is_order_tagged_to_basket(order: Dict[str, Any], basket_symbols: List[str], basket_order_ids: Optional[List[str]] = None) -> bool:
    """Check if an order belongs to a basket based on symbol match or order ID.

    Args:
        order: Order dict from get_equity_orders.
        basket_symbols: List of symbols in the basket.
        basket_order_ids: List of specific order UUIDs associated with the basket.

    Returns:
        True if the order matches the basket's symbols or order IDs.
    """
    if not order:
        return False

    order_id = order.get("id")
    if basket_order_ids and order_id in basket_order_ids:
        return True

    symbol = order.get("symbol")
    return bool(symbol and symbol in basket_symbols)


def encode_watchlist_metadata(
    slug: str,
    target_weights: Dict[str, float],
    rebalance_threshold_pct: float = 5.0,
    description: str = "",
    snapshot: Optional[Dict[str, Any]] = None,
) -> str:
    """Encode basket metadata into ultra-compact Watchlist display_description string.

    Format:
      {"s":"slug","t":1721861640,"th":5,"w":{"WDC":[30,15.0,55.0],"STX":[30,10.0,92.0]}}
      where symbol mapping value is [target_weight_pct, shares, avg_cost].

    Args:
        slug: Basket slug identifier.
        target_weights: Dict mapping symbol to target weight percentage (e.g. {'WDC': 30, 'STX': 30}).
        rebalance_threshold_pct: Rebalance threshold percentage.
        description: Optional human-readable description (omitted if using ultra-compact JSON).
        snapshot: Optional snapshot dict with keys 'ts' (timestamp) and 'h' (holdings dict).

    Returns:
        Ultra-compact JSON string (always under 255 chars for up to 12 symbols).
    """
    ts_val = 0
    snap_h = {}
    if snapshot:
        snap_ts = snapshot.get("ts")
        if isinstance(snap_ts, str):
            try:
                dt = datetime.datetime.fromisoformat(snap_ts.replace("Z", "+00:00"))
                ts_val = int(dt.timestamp())
            except ValueError:
                ts_val = int(time.time())
        elif isinstance(snap_ts, (int, float)):
            ts_val = int(snap_ts)
        snap_h = snapshot.get("h", {})

    symbol_map = {}
    for sym, weight in target_weights.items():
        sym = sym.upper()
        weight_val = round(float(weight), 2)
        if weight_val == int(weight_val):
            weight_val = int(weight_val)

        h_detail = snap_h.get(sym)
        if h_detail:
            if isinstance(h_detail, dict):
                shares = float(h_detail.get("shares", 0.0))
                avg_cost = float(h_detail.get("avg_cost", 0.0))
            elif isinstance(h_detail, (list, tuple)) and len(h_detail) >= 2:
                shares = float(h_detail[0])
                avg_cost = float(h_detail[1])
            else:
                shares, avg_cost = 0.0, 0.0

            shares_val = round(shares, 5)
            cost_val = round(avg_cost, 2)
            symbol_map[sym] = [weight_val, shares_val, cost_val]
        else:
            symbol_map[sym] = [weight_val]

    clean_slug = slug
    if len(clean_slug) > 24:
        clean_slug = clean_slug[:24]

    metadata: Dict[str, Any] = {
        "s": clean_slug,
        "w": symbol_map,
    }
    if ts_val > 0:
        metadata["t"] = ts_val
    if rebalance_threshold_pct != 5.0:
        metadata["th"] = round(rebalance_threshold_pct, 1)

    return json.dumps(metadata, separators=(",", ":"))


def decode_watchlist_metadata(display_description: Optional[str]) -> Optional[Dict[str, Any]]:
    """Extract and decode basket metadata JSON from Watchlist display_description.

    Handles both ultra-compact format {"s":...,"w":{...}} and legacy [BASKET_MODEL] format.

    Args:
        display_description: Watchlist description string from Robinhood.

    Returns:
        Dict containing decoded basket metadata.
    """
    if not display_description:
        return None

    desc_str = display_description.strip()
    if desc_str.startswith("[BASKET_MODEL]"):
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
    except json.JSONDecodeError:
        return None

    # Handle ultra-compact schema
    if "s" in data and "w" in data:
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
            iso_ts = datetime.datetime.fromtimestamp(ts_val, tz=datetime.timezone.utc).isoformat()

        snapshot = None
        if iso_ts and snap_h:
            snapshot = {"ts": iso_ts, "h": snap_h}

        return {
            "slug": slug,
            "weights": target_weights,
            "threshold": float(data.get("th", 5.0)),
            "snapshot": snapshot,
        }

    # Handle legacy schema
    return data


def reconstruct_basket_positions(
    orders: List[Dict[str, Any]],
    basket_symbols: Optional[List[str]] = None,
    snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Dict[str, float]]:
    """Reconstruct net holdings and average cost basis from filled equity orders.

    Optionally starts from a baseline snapshot and applies orders created after snapshot['ts'].

    Args:
        orders: List of equity order dicts returned by get_equity_orders.
        basket_symbols: Restrict calculations to symbols in this basket.
        snapshot: Dict with baseline snapshot: {'ts': '...', 'h': {'SYMBOL': {'shares': X, 'avg_cost': Y}}}

    Returns:
        Dict mapping symbol to holding info:
        {
          "SYMBOL": {
            "shares": float,
            "total_cost": float,
            "avg_cost": float
          }
        }
    """
    holdings: Dict[str, Dict[str, float]] = {}

    # Initialize from snapshot baseline if available
    snapshot_ts = None
    if snapshot:
        snapshot_ts = snapshot.get("ts")
        snap_holdings = snapshot.get("h", {})
        for sym, detail in snap_holdings.items():
            if not basket_symbols or sym in basket_symbols:
                if isinstance(detail, dict):
                    holdings[sym] = {
                        "shares": float(detail.get("shares", 0.0)),
                        "total_cost": float(detail.get("total_cost", 0.0)),
                        "avg_cost": float(detail.get("avg_cost", 0.0)),
                    }
                elif isinstance(detail, (list, tuple)) and len(detail) >= 2:
                    shares = float(detail[0])
                    avg_cost = float(detail[1])
                    holdings[sym] = {
                        "shares": shares,
                        "total_cost": shares * avg_cost,
                        "avg_cost": avg_cost,
                    }

    for order in orders:
        state = order.get("state")
        if state != "filled":
            continue

        symbol = order.get("symbol")
        if not symbol:
            continue

        if basket_symbols and symbol not in basket_symbols:
            continue

        # If a snapshot timestamp exists, skip orders created before or at snapshot timestamp
        created_at = order.get("created_at")
        if snapshot_ts and created_at and created_at <= snapshot_ts:
            continue

        try:
            qty = float(order.get("quantity", order.get("cumulative_quantity", 0)))
            price = float(order.get("average_price", order.get("price", 0)))
        except (ValueError, TypeError):
            continue

        side = order.get("side", "").lower()

        if symbol not in holdings:
            holdings[symbol] = {"shares": 0.0, "total_cost": 0.0, "avg_cost": 0.0}

        curr = holdings[symbol]
        if side == "buy":
            curr["shares"] += qty
            curr["total_cost"] += qty * price
        elif side == "sell":
            if curr["shares"] > 0:
                avg_cost = curr["total_cost"] / curr["shares"]
                curr["shares"] = max(0.0, curr["shares"] - qty)
                curr["total_cost"] = curr["shares"] * avg_cost

        if curr["shares"] > 0:
            curr["avg_cost"] = curr["total_cost"] / curr["shares"]
        else:
            curr["avg_cost"] = 0.0
            curr["total_cost"] = 0.0

    return holdings


def update_basket_watchlist_baseline(
    display_description: str,
    symbol: str,
    side: str,
    shares: float,
    price: float,
    timestamp: Optional[float] = None,
) -> str:
    """Calculate and return updated Watchlist display_description after a trade fill.

    Performs an in-place baseline snapshot update so metadata size never grows.

    Args:
        display_description: Current Watchlist description containing metadata JSON.
        symbol: Ticker symbol of the filled trade (e.g. 'WDC').
        side: Trade side ('buy' or 'sell').
        shares: Shares filled.
        price: Price per share at fill.
        timestamp: Optional fill timestamp (defaults to current time).

    Returns:
        Updated ultra-compact display_description string ready for update_watchlist.
    """
    metadata = decode_watchlist_metadata(display_description)
    if not metadata:
        raise ValueError("Invalid basket metadata in display_description")

    slug = metadata.get("slug", "basket")
    target_weights = metadata.get("weights", {})
    threshold = metadata.get("threshold", 5.0)
    snapshot = metadata.get("snapshot") or {}

    snap_h = snapshot.get("h", {})
    symbol = symbol.upper()

    curr_shares, curr_avg_cost = 0.0, 0.0
    if symbol in snap_h:
        detail = snap_h[symbol]
        if isinstance(detail, (list, tuple)) and len(detail) >= 2:
            curr_shares, curr_avg_cost = float(detail[0]), float(detail[1])

    side_lower = side.lower()
    if side_lower == "buy":
        new_total_cost = (curr_shares * curr_avg_cost) + (shares * price)
        new_shares = curr_shares + shares
        new_avg_cost = new_total_cost / new_shares if new_shares > 0 else 0.0
    elif side_lower == "sell":
        new_shares = max(0.0, curr_shares - shares)
        new_avg_cost = curr_avg_cost if new_shares > 0 else 0.0
    else:
        raise ValueError(f"Invalid side '{side}', must be 'buy' or 'sell'")

    snap_h[symbol] = [round(new_shares, 6), round(new_avg_cost, 4)]
    now_ts = timestamp or time.time()

    new_snapshot = {"ts": int(now_ts), "h": snap_h}
    return encode_watchlist_metadata(
        slug=slug,
        target_weights=target_weights,
        rebalance_threshold_pct=threshold,
        snapshot=new_snapshot,
    )


def watchlist_to_basket_dict(
    display_name: str,
    display_description: str,
    reconstructed_positions: Optional[Dict[str, Dict[str, float]]] = None,
) -> Dict[str, Any]:
    """Transform Robinhood Watchlist data into standard Basket dictionary format.

    Enables calc_performance.py and calc_drift.py to consume cloud Watchlists directly.

    Args:
        display_name: Watchlist display name (e.g. 'Basket: Storage Leaders').
        display_description: Watchlist description containing metadata.
        reconstructed_positions: Holding positions from reconstruct_basket_positions.

    Returns:
        Dict matching standard basket JSON schema.
    """
    metadata = decode_watchlist_metadata(display_description) or {}

    clean_name = display_name
    if clean_name.startswith("Basket:"):
        clean_name = clean_name[7:].strip()

    slug = metadata.get("slug", clean_name.lower().replace(" ", "-"))
    target_weights = metadata.get("weights", {})
    threshold = metadata.get("threshold", 5.0)

    # Use reconstructed positions or fallback to metadata snapshot baseline
    positions_source = reconstructed_positions
    if positions_source is None and metadata.get("snapshot"):
        snap_h = metadata["snapshot"].get("h", {})
        positions_source = {}
        for sym, detail in snap_h.items():
            if isinstance(detail, (list, tuple)) and len(detail) >= 2:
                sh = float(detail[0])
                c = float(detail[1])
                positions_source[sym] = {"shares": sh, "avg_cost": c, "total_cost": sh * c}

    holdings_list = []
    total_invested = 0.0

    for sym, weight in target_weights.items():
        pos_data = None
        if positions_source and sym in positions_source:
            h_info = positions_source[sym]
            shares = h_info.get("shares", 0.0)
            avg_cost = h_info.get("avg_cost", 0.0)
            tot = h_info.get("total_cost", shares * avg_cost)
            if shares > 0:
                pos_data = {
                    "shares": shares,
                    "avg_cost": avg_cost,
                    "total_invested": round(tot, 2),
                }
                total_invested += tot

        holdings_list.append({
            "symbol": sym,
            "target_weight_pct": weight,
            "position": pos_data,
        })

    return {
        "name": clean_name,
        "slug": slug,
        "total_invested": round(total_invested, 2),
        "rebalance_threshold_pct": threshold,
        "holdings": holdings_list,
    }
