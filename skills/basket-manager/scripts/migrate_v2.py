#!/usr/bin/env python3
"""Stage 2 migration: move the user's live cloud baskets into the local store.

Reads three inputs as JSON — the `get_watchlists` response, the
`get_equity_orders` response, and the legacy `data/baskets/*.json` files —
and writes events through `basket_events`. It never opens the event log
itself, and it never touches the network: everything arrives as arguments.

Order data is authoritative for shares and prices. The `Z64:` snapshot
embedded in each watchlist's description is a lossy 5-decimal copy; it is
used only to decide which basket an order belongs to. See
docs/superpowers/plans/2026-07-29-local-basket-storage-stage2.md.
"""

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from basket_utils import decode_watchlist_metadata
from basket_weights import normalize_weights

# An order of magnitude above the largest observed drift between a snapshot
# claim and the order that produced it (IPGP, 0.000106), and far below the
# smallest gap between two competing claims for the same symbol (LITE,
# 0.018). See the plan's "Attribution, resolved against the real data".
SHARE_TOLERANCE = 0.0005

BASKET_NAME_PREFIX = "Basket: "


# --- reading the sources -----------------------------------------------------

def _unwrap_list(payload, *keys):
    """Peel a {"data": {"<key>": [...]}} envelope down to the bare list.

    Accepts the raw MCP response, a one-level envelope, or a bare list, so
    callers do not need to know which shape they were handed.
    """
    data = payload
    if isinstance(data, dict) and "data" in data:
        data = data["data"]
    if isinstance(data, dict):
        for key in keys:
            if key in data:
                return data[key] or []
        return []
    if isinstance(data, list):
        return data
    return []


def read_baskets(watchlists_response):
    """Return one dict per basket-bearing watchlist.

    Each dict carries `slug` (the metadata's own, possibly-truncated slug —
    NOT regenerated here), `name` (display name minus a leading "Basket: "),
    `weights`, `snapshot` (symbol -> [shares, avg_cost], or {} when the
    watchlist carries no positions yet), and `threshold`.

    A watchlist whose description does not decode as basket metadata is an
    ordinary list and is skipped.
    """
    baskets = []
    for watchlist in _unwrap_list(watchlists_response, "watchlists", "results"):
        if not isinstance(watchlist, dict):
            continue
        metadata = decode_watchlist_metadata(watchlist.get("display_description"))
        if not metadata:
            continue

        name = watchlist.get("display_name") or ""
        if name.startswith(BASKET_NAME_PREFIX):
            name = name[len(BASKET_NAME_PREFIX):]

        snapshot = metadata.get("snapshot") or {}
        snap_h = snapshot.get("h") or {} if isinstance(snapshot, dict) else {}

        baskets.append({
            "slug": metadata.get("slug"),
            "name": name,
            "weights": dict(metadata.get("weights") or {}),
            "snapshot": dict(snap_h),
            "threshold": metadata.get("threshold", 5.0),
        })
    return baskets


def normalize_basket_weights(weights):
    """Normalize a basket's weights and report whether anything changed."""
    normalized = normalize_weights(weights)
    changed = set(normalized) != set(weights) or any(
        normalized[symbol] != weights[symbol] for symbol in normalized
    )
    return normalized, changed


# --- attribution --------------------------------------------------------------

def _order_shares(order):
    """Return the filled share count of an order, or 0.0.

    `cumulative_quantity` wins; `quantity` is the fallback. Robinhood returns
    `quantity: null` on a dollar-amount order and carries the real figure in
    `cumulative_quantity`.
    """
    for key in ("cumulative_quantity", "quantity"):
        raw = order.get(key)
        if raw in (None, ""):
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0.0


def _claimed_shares(snapshot_entry):
    """Return the share count a snapshot entry claims, or 0.0."""
    if isinstance(snapshot_entry, (list, tuple)) and snapshot_entry:
        try:
            return float(snapshot_entry[0])
        except (TypeError, ValueError):
            return 0.0
    if isinstance(snapshot_entry, dict):
        try:
            return float(snapshot_entry.get("shares", 0.0))
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def attribute_orders(baskets, orders):
    """Attribute each filled order to at most one basket.

    For each filled order, every basket whose weight set contains the
    order's symbol AND whose snapshot claims a non-zero share count for it
    is a candidate. Each candidate is scored by
    `abs(order_shares - claimed_shares)`. Every (order, basket) pair within
    `SHARE_TOLERANCE` is a contender; contenders are resolved globally,
    closest match first, so a closer match wins a contested claim and no
    order is assigned twice.

    A basket with no snapshot entry for a symbol claims nothing for it and
    can never win that symbol's orders, even if the symbol is in its
    weight set (see: semiconductor-etf-style, which holds NVDA and MU but
    carries no snapshot at all).

    Returns (assignments, unattributed): `assignments` maps order id to the
    winning basket's (metadata) slug; `unattributed` is the list of filled
    orders — the full order dict — that matched no candidate.
    """
    contenders = []
    filled_orders = []

    for order in orders:
        if not isinstance(order, dict) or order.get("state") != "filled":
            continue
        order_id = order.get("id")
        symbol = order.get("symbol")
        if not order_id or not symbol:
            continue
        shares = _order_shares(order)
        if shares <= 0:
            continue
        filled_orders.append(order)

        for basket in baskets:
            if symbol not in basket["weights"]:
                continue
            claimed = _claimed_shares(basket["snapshot"].get(symbol))
            if claimed <= 0:
                continue
            diff = abs(shares - claimed)
            if diff <= SHARE_TOLERANCE:
                contenders.append((diff, order_id, basket["slug"]))

    # Closest match first; ties break deterministically on order id then slug
    # so the result never depends on dict/list iteration order.
    contenders.sort(key=lambda c: (c[0], c[1], c[2]))

    assignments = {}
    for _diff, order_id, slug in contenders:
        if order_id in assignments:
            continue
        assignments[order_id] = slug

    unattributed = [o for o in filled_orders if o.get("id") not in assignments]
    return assignments, unattributed
