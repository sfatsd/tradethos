#!/usr/bin/env python3
"""Replay the event log into basket state.

Every read replays the log. The store holds no cache. This module owns the
average-cost arithmetic and the order-id dedupe rule.
"""

import re
from collections import OrderedDict

from basket_events import utc_now

SCHEMA_VERSION = 2
TRADE_TYPES = ("buy", "sell")


class StoreError(Exception):
    """The log cannot be replayed into a valid state."""


def slugify(name):
    """Build a slug from a basket name."""
    text = name.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


class Position(object):
    """The shares of one symbol that one basket holds."""

    def __init__(self, shares=0.0, avg_cost=0.0, realized_pnl=0.0):
        self.shares = shares
        self.avg_cost = avg_cost
        self.realized_pnl = realized_pnl

    @property
    def total_invested(self):
        return self.shares * self.avg_cost

    def buy(self, shares, price):
        """Apply a purchase with the average cost method."""
        total_cost = self.total_invested + (shares * price)
        self.shares = self.shares + shares
        self.avg_cost = total_cost / self.shares if self.shares > 0 else 0.0

    def sell(self, shares, price):
        """Apply a sale with the average cost method.

        The average cost does not change. The gain or loss goes to
        realized_pnl. A sale of every share leaves the average cost at zero.

        Returns the share count that it could not cover. A caller that gets a
        non-zero result must report it; a silent clamp would credit a profit
        on shares the basket never held.
        """
        oversold = 0.0
        if shares > self.shares + 1e-12:
            oversold = shares - self.shares
            shares = self.shares

        self.realized_pnl += (price - self.avg_cost) * shares
        self.shares = self.shares - shares
        if self.shares <= 1e-12:
            self.shares = 0.0
            self.avg_cost = 0.0
        return oversold


class Holding(object):
    """One symbol in a basket: its target weight and its position."""

    def __init__(self, symbol, target_weight_pct=0, thesis=""):
        self.symbol = symbol
        self.target_weight_pct = target_weight_pct
        self.thesis = thesis
        self.position = Position()

    @property
    def has_position(self):
        return self.position.shares > 0


class Basket(object):
    """One basket, rebuilt from its events."""

    def __init__(self, slug):
        self.slug = slug
        self.name = slug
        self.description = ""
        self.account_number = ""
        self.rebalance_threshold_pct = 5.0
        self.created_at = ""
        self.updated_at = ""
        self.holdings = OrderedDict()

    @property
    def target_weights(self):
        return dict((s, h.target_weight_pct) for s, h in self.holdings.items())

    @property
    def total_invested(self):
        return sum(h.position.total_invested for h in self.holdings.values())

    @property
    def realized_pnl(self):
        return sum(h.position.realized_pnl for h in self.holdings.values())


class ReplayResult(object):
    """The outcome of a replay."""

    def __init__(self, baskets, ignored, order_index, clamped=None):
        self.baskets = baskets
        self.ignored = ignored
        self.order_index = order_index
        self.clamped = clamped if clamped is not None else []


def replay(events):
    """Build every live basket from the events, in log order.

    A `basket_deleted` event ends a lifetime. A later `basket_created` event
    with the same slug starts a new one, and the old events stay with the old
    basket.

    A `buy` or `sell` event whose `order_id` already appeared is ignored. The
    first event wins. Every ignored event goes into the result.
    """
    baskets = OrderedDict()
    ignored = []
    order_index = {}
    clamped = []

    for event in events:
        slug = event.get("slug")
        event_type = event.get("type")
        if not slug or not event_type:
            continue

        if event_type == "basket_created":
            basket = Basket(slug)
            basket.name = event.get("name", slug)
            basket.description = event.get("description", "")
            basket.account_number = event.get("account_number", "")
            basket.rebalance_threshold_pct = event.get(
                "rebalance_threshold_pct", 5.0)
            basket.created_at = event.get("ts", "")
            basket.updated_at = event.get("ts", "")
            baskets[slug] = basket
            continue

        basket = baskets.get(slug)
        if basket is None:
            # The basket does not exist in this lifetime. The event belongs to
            # a deleted basket, so the live state ignores it.
            continue
        basket.updated_at = event.get("ts", basket.updated_at)

        if event_type == "basket_deleted":
            del baskets[slug]

        elif event_type == "basket_updated":
            field = event.get("field")
            if field in ("name", "description", "rebalance_threshold_pct"):
                setattr(basket, field, event.get("to"))

        elif event_type == "holding_added":
            symbol = event.get("symbol")
            if symbol and symbol not in basket.holdings:
                basket.holdings[symbol] = Holding(
                    symbol, event.get("weight", 0), event.get("thesis", ""))

        elif event_type == "holding_removed":
            basket.holdings.pop(event.get("symbol"), None)

        elif event_type == "weight_changed":
            holding = basket.holdings.get(event.get("symbol"))
            if holding is not None:
                holding.target_weight_pct = event.get("to")

        elif event_type == "thesis_changed":
            holding = basket.holdings.get(event.get("symbol"))
            if holding is not None:
                holding.thesis = event.get("thesis", "")

        elif event_type in TRADE_TYPES:
            order_id = event.get("order_id")
            if order_id and order_id in order_index:
                ignored.append({
                    "line": event.get("_line"),
                    "order_id": order_id,
                    "slug": slug,
                    "kept_by": order_index[order_id],
                })
                continue

            holding = basket.holdings.get(event.get("symbol"))
            if holding is None:
                continue

            shares = float(event.get("shares", 0.0))
            price = float(event.get("price", 0.0))
            if event_type == "buy":
                holding.position.buy(shares, price)
            else:
                oversold = holding.position.sell(shares, price)
                if oversold > 0:
                    clamped.append({
                        "line": event.get("_line"),
                        "order_id": order_id,
                        "slug": slug,
                        "symbol": event.get("symbol"),
                        "requested": shares,
                        "oversold": oversold,
                    })

            if order_id:
                order_index[order_id] = slug

    return ReplayResult(baskets, ignored, order_index, clamped)


def snapshot_dict(basket):
    """Build the export file contents for one basket."""
    holdings = []
    for holding in basket.holdings.values():
        position = None
        # A holding that was bought and then fully sold has shares == 0, so
        # has_position (shares > 0) is False - but its realized_pnl must
        # still show here, or a profit or loss vanishes from the one place a
        # user checks a single symbol. basket.realized_pnl (the total) never
        # loses it either way; only the per-holding breakdown is at risk. A
        # holding that was never bought has realized_pnl == 0.0 too, so it
        # still reports position: null.
        if holding.has_position or holding.position.realized_pnl != 0:
            position = {
                "shares": round(holding.position.shares, 6),
                "avg_cost": round(holding.position.avg_cost, 4),
                "total_invested": round(holding.position.total_invested, 2),
                "realized_pnl": round(holding.position.realized_pnl, 2),
            }
        holdings.append({
            "symbol": holding.symbol,
            "target_weight_pct": holding.target_weight_pct,
            "thesis": holding.thesis,
            "position": position,
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "name": basket.name,
        "slug": basket.slug,
        "description": basket.description,
        "account_number": basket.account_number,
        "rebalance_threshold_pct": basket.rebalance_threshold_pct,
        "created_at": basket.created_at,
        "updated_at": basket.updated_at,
        "holdings": holdings,
        "totals": {
            "total_invested": round(basket.total_invested, 2),
            "realized_pnl": round(basket.realized_pnl, 2),
            "built_at": utc_now(),
        },
    }
