#!/usr/bin/env python3
"""Utility module for Tradethos Cloud-Native Basket Management.

Provides helper functions for:
1. Encoding/decoding basket metadata into zlib-compressed Base64 Robinhood Watchlist descriptions.
2. Reconstructing basket holdings & average cost from filled orders + baseline snapshots.
3. Performing in-place baseline updates after trade fills so metadata size never grows.
4. Converting Watchlist data into standard Basket dictionaries for performance/drift scripts.
"""

import base64
import datetime
import json
import math
import re
import time
import zlib
from typing import Any, Dict, Iterator, List, Optional, Tuple

# Robinhood caps watchlist display_description at 256 characters.
MAX_DESCRIPTION_LENGTH = 256

# Slugs are truncated to keep the compressed payload small.
MAX_SLUG_LENGTH = 24


_FRACTIONAL_SECONDS_RE = re.compile(r"\.(\d+)")


def _normalize_iso(text: str) -> str:
    """Coerce an ISO 8601 string into a form fromisoformat accepts on Python < 3.11.

    Older versions reject the 'Z' suffix and accept only 3- or 6-digit fractional
    seconds, while Robinhood emits both 'Z' and variable-precision fractions.
    """
    text = text.strip().replace("Z", "+00:00")
    match = _FRACTIONAL_SECONDS_RE.search(text)
    if match:
        digits = match.group(1)[:6].ljust(6, "0")
        text = f"{text[:match.start()]}.{digits}{text[match.end():]}"
    return text


def _to_epoch(value: Any) -> Optional[float]:
    """Normalize an int/float epoch or ISO 8601 string into epoch seconds.

    Naive timestamps are treated as UTC. Returns None when the value cannot be parsed.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            dt = datetime.datetime.fromisoformat(_normalize_iso(value))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.timestamp()
    return None


def _first_number(order: Dict[str, Any], *keys: str) -> float:
    """Return the first key whose value parses as a number, treating None/'' as absent.

    Robinhood returns `quantity: null` on dollar-amount orders and carries the real
    figure in `cumulative_quantity`, so a plain dict.get() fallback is not enough.
    """
    for key in keys:
        raw = order.get(key)
        if raw is None or raw == "":
            continue
        try:
            return float(raw)
        except (ValueError, TypeError):
            continue
    return 0.0


def _order_fill_price(order: Dict[str, Any]) -> float:
    """Return the per-share price an order actually filled at.

    Prefers `average_price`, then a quantity-weighted mean of `executions`.
    Deliberately does NOT fall back to `price`: on a Robinhood order that field
    is the limit/reference price, not the fill price, and the two differ in live
    data (e.g. NVDA price 206.80 vs average_price 208.04). Using it would book a
    wrong cost basis, which is worse than skipping the order.
    """
    price = _first_number(order, "average_price")
    if price > 0:
        return price

    total_qty = 0.0
    total_cost = 0.0
    for execution in order.get("executions") or []:
        if not isinstance(execution, dict):
            continue
        exec_qty = _first_number(execution, "quantity")
        exec_price = _first_number(execution, "price")
        if exec_qty > 0 and exec_price > 0:
            total_qty += exec_qty
            total_cost += exec_qty * exec_price
    return total_cost / total_qty if total_qty > 0 else 0.0


def encode_watchlist_metadata(
    slug: str,
    target_weights: Dict[str, float],
    rebalance_threshold_pct: float = 5.0,
    snapshot: Optional[Dict[str, Any]] = None,
    max_length: int = MAX_DESCRIPTION_LENGTH,
) -> str:
    """Encode basket metadata into zlib-compressed Base64 display_description string.

    Uses zlib compression + Base64 encoding ('Z64:') to pack basket metadata into
    Robinhood's 256-character display_description limit.

    Capacity depends heavily on whether a holdings snapshot is present. Weights
    alone compress to 30+ symbols, but each snapshot entry costs roughly 20 more
    characters: a live 7-symbol basket with full snapshot measures 240 of 256.
    Budget for ~10 symbols once positions exist, and expect ValueError beyond that.

    Args:
        slug: Basket slug identifier.
        target_weights: Dict mapping symbol to target weight percentage (e.g. {'WDC': 30, 'STX': 30}).
        rebalance_threshold_pct: Rebalance threshold percentage.
        snapshot: Optional snapshot dict with keys 'ts' (timestamp) and 'h' (holdings dict).
        max_length: Reject results longer than this (Robinhood's limit is 256).

    Returns:
        Compressed Base64 string prefixed with 'Z64:'.

    Raises:
        ValueError: If the encoded result exceeds max_length, which Robinhood would
            reject with an opaque API error.
    """
    ts_val = 0
    snap_h = {}
    if snapshot:
        ts_epoch = _to_epoch(snapshot.get("ts"))
        if ts_epoch is None and snapshot.get("ts") is not None:
            ts_epoch = time.time()
        ts_val = int(ts_epoch) if ts_epoch else 0
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

    clean_slug = slug[:MAX_SLUG_LENGTH]

    metadata: Dict[str, Any] = {
        "s": clean_slug,
        "w": symbol_map,
    }
    if ts_val > 0:
        metadata["t"] = ts_val
    if rebalance_threshold_pct != 5.0:
        metadata["th"] = round(rebalance_threshold_pct, 1)

    json_str = json.dumps(metadata, separators=(",", ":"))
    compressed = zlib.compress(json_str.encode("utf-8"))
    b64_str = base64.b64encode(compressed).decode("utf-8")
    encoded = f"Z64:{b64_str}"

    if len(encoded) > max_length:
        raise ValueError(
            f"Encoded basket metadata is {len(encoded)} chars, exceeding the "
            f"{max_length}-char watchlist description limit "
            f"({len(target_weights)} symbols, {len(snap_h)} with holdings). "
            "Reduce the number of symbols or split the basket."
        )
    return encoded


def decode_watchlist_metadata(display_description: Optional[str]) -> Optional[Dict[str, Any]]:
    """Extract and decode zlib-compressed basket metadata JSON from Watchlist display_description.

    Handles 'Z64:' compressed Base64, raw JSON {"s":...}, and legacy [BASKET_MODEL] formats.

    Args:
        display_description: Watchlist description string from Robinhood.

    Returns:
        Dict containing decoded basket metadata.
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


def unwrap_payload(data: Any, *keys: str) -> Any:
    """Peel MCP response envelopes off a payload.

    get_watchlists and get_equity_orders both return {"data": {"<key>": [...]}},
    so unwrap "data" first, then the named list key. A bare list passes through.
    """
    if isinstance(data, dict) and "data" in data:
        data = data["data"]
    if isinstance(data, dict):
        for key in keys:
            if key in data:
                return data[key]
    return data


def parse_watchlists_json(raw: str) -> List[Dict[str, Any]]:
    """Parse a --watchlists-json CLI argument into a list of watchlist dicts.

    Accepts the raw get_watchlists response ({"data": {"watchlists": [...]}}),
    a bare {"watchlists": [...]} / {"results": [...]} envelope, or a plain array.
    Non-dict elements are dropped so a stray symbol list fails loudly here rather
    than as an AttributeError deeper in.

    Raises:
        json.JSONDecodeError: If the argument is not valid JSON.
        ValueError: If the payload is not a list of watchlist objects.
    """
    data = unwrap_payload(json.loads(raw), "watchlists", "results")
    if not isinstance(data, list):
        raise ValueError(
            "Expected a list of watchlists — pass the get_watchlists response "
            "({'data': {'watchlists': [...]}}), a {'watchlists': [...]} envelope, "
            "or a bare JSON array"
        )
    if data and not any(isinstance(item, dict) for item in data):
        raise ValueError(
            "Expected watchlist objects, got a list of "
            f"{type(data[0]).__name__} — did you pass a symbol list by mistake?"
        )
    return data


def iter_watchlist_baskets(
    watchlists: List[Dict[str, Any]],
) -> Iterator[Tuple[str, str, Dict[str, Any]]]:
    """Yield (display_name, display_description, metadata) for basket-bearing watchlists.

    Watchlists whose description carries no decodable basket metadata are skipped,
    so ordinary (non-basket) watchlists pass through harmlessly. Robinhood omits
    display_description entirely on lists that have never had one set.
    """
    for wl in watchlists:
        if not isinstance(wl, dict):
            continue
        desc = wl.get("display_description") or ""
        metadata = decode_watchlist_metadata(desc)
        if not metadata:
            continue
        yield wl.get("display_name", "Unknown"), desc, metadata


def reconstruct_basket_positions(
    orders: List[Dict[str, Any]],
    basket_symbols: Optional[List[str]] = None,
    snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Dict[str, float]]:
    """Recovery tool: reconstruct holdings from order history when baseline snapshot is stale or missing.

    This function is NOT the primary position source — the Z64 baseline snapshot is.
    Use this only when:
    - A baseline snapshot update failed after a trade fill
    - Snapshot data appears inconsistent with actual Robinhood positions
    - Manual reconciliation is needed

    IMPORTANT: Always pass a snapshot to avoid double-counting for overlapping symbols.
    Without a snapshot, all filled orders for matching symbols are counted regardless of
    which basket they were intended for.

    Starts from the baseline snapshot and applies only filled orders created after snapshot['ts'].

    Args:
        orders: List of equity order dicts returned by get_equity_orders.
        basket_symbols: Restrict calculations to symbols in this basket.
        snapshot: Dict with baseline snapshot: {'ts': '...', 'h': {'SYMBOL': [shares, avg_cost]}}
                  STRONGLY RECOMMENDED to avoid overlap double-counting.

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
        snapshot_ts = _to_epoch(snapshot.get("ts"))
        if snapshot.get("ts") is not None and snapshot_ts is None:
            # Falling through with snapshot_ts=None would disable the cutoff and
            # replay the entire order history on top of a populated baseline.
            raise ValueError(
                f"Snapshot timestamp {snapshot.get('ts')!r} could not be parsed; "
                "refusing to replay orders without a cutoff (this would double-count)."
            )
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

        # Skip orders that filled at or before the snapshot. Use the fill time, not
        # created_at: a limit order can be created days before it fills (observed
        # live: created 2026-07-25, filled 2026-07-27), and filtering on creation
        # silently drops fills that happened after the snapshot was taken.
        filled_ts = _to_epoch(order.get("last_transaction_at")) or _to_epoch(order.get("created_at"))
        if snapshot_ts and filled_ts is not None and filled_ts <= snapshot_ts:
            continue

        qty = _first_number(order, "cumulative_quantity", "quantity")
        price = _order_fill_price(order)
        if qty <= 0 or price <= 0:
            continue

        side = (order.get("side") or "").lower()

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

    # Round the stored second UP. Flooring puts the snapshot marginally *before*
    # the fill that produced it, so replaying that same order would apply it a
    # second time. Robinhood fill timestamps carry sub-second precision, so this
    # is reachable on any fill that lands mid-second.
    new_snapshot = {"ts": math.ceil(now_ts), "h": snap_h}
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
