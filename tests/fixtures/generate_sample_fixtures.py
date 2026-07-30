#!/usr/bin/env python3
"""Regenerate the synthetic migration fixtures.

`sample_watchlists.json` and `sample_orders.json` stand in for a
`get_watchlists` response and a `get_equity_orders` response. Every figure in
them is invented. They replaced a pair of fixtures captured from a real
brokerage account, which had no business in a public repository.

The two files are one fixture in two parts and MUST be regenerated together:
the orders' quantities are derived from the watchlist snapshots' claims, and
the attribution tests turn on the exact gaps between them. Run:

    python3 tests/fixtures/generate_sample_fixtures.py

The synthetic data deliberately reproduces every structural property the
tests in tests/test_migrate_v2.py depend on:

- nine watchlists, of which six decode as baskets and three are ordinary
  lists that must be skipped;
- three ways of carrying basket metadata: the `Z64:` compressed blob, and
  bare JSON (SUM_120 below);
- two ways of having no snapshot: entries that carry a weight only
  (BROAD_SEMIS), and entries that carry shares but no `t` timestamp
  (SUM_120);
- two baskets whose `item_count` is 0 even though they carry full metadata;
- one basket whose weights sum to 120, so normalization still has work to do;
- one stored slug truncated at exactly 24 characters that IS a genuine
  truncation of its display name, and one that is exactly 24 characters but
  was never truncated -- resolve_slug must tell them apart;
- one symbol (LSER) claimed by two baskets, with two orders whose quantities
  match the two different claims. This is what pins the attribution logic;
- two symbols (ALFA, MEMX) that appear in two baskets where only one of the
  two claims any shares;
- exactly one order (the stray ALFA buy) that matches no claim at all;
- snapshot shares that differ from the order quantities by 0.000002 to
  0.000112 shares, so the 0.0005 tolerance still has to absorb real drift
  while staying far below the 0.018 gap between the two LSER claims.
"""

import base64
import json
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Every watchlist in the old cloud format offered the same object types.
ALLOWED_OBJECT_TYPES = [
    "currency_pair",
    "futures",
    "futures_product",
    "index",
    "instrument",
    "tokenized_stock",
]

# One shared epoch for every snapshot: 2026-03-12T18:00:00Z.
SNAPSHOT_EPOCH = 1773338400


def encode_z64(payload):
    """Encode basket metadata the way the old cloud format did.

    `decode_watchlist_metadata` in migrate_v2.py is the reader this has to
    satisfy: a "Z64:" prefix, then Base64 of zlib-compressed JSON. The
    payload shape is {"s": slug, "w": {SYM: [weight, shares, avg_cost]},
    "t": epoch}, where an entry may also be just [weight] when the basket
    carries no positions.
    """
    text = json.dumps(payload, separators=(",", ":"))
    return "Z64:" + base64.b64encode(zlib.compress(text.encode("utf-8"))).decode("ascii")


# --- the six baskets ---------------------------------------------------------
#
# Each entry is (stored_slug, display_name, item_count, weights, snapshot,
# include_timestamp, encoder). `weights` maps symbol -> target percent;
# `snapshot` maps symbol -> (shares, avg_cost) and may be empty.

# The Core Growth analogue: seven weights of 14.29/14.28 that sum to exactly
# 100.0 but are not whole numbers, so normalization must move two of them to
# 15. Alphabetically, ALFA and BETA hold the joint-largest remainders and take
# the two spare percent.
CORE_GROWTH = {
    "slug": "core-growth-index",
    "name": "Core Growth Index",
    "item_count": 7,
    "weights": {
        "ALFA": 14.29, "BETA": 14.29, "GAMA": 14.29, "DLTA": 14.29,
        "EPSI": 14.28, "ZETA": 14.28, "IOTA": 14.28,
    },
    "snapshot": {
        "ALFA": (0.07120, 210.55), "BETA": (0.03910, 385.20),
        "GAMA": (0.04630, 325.40), "DLTA": (0.04710, 320.10),
        "EPSI": (0.06340, 236.80), "ZETA": (0.02440, 612.75),
        "IOTA": (0.12730, 118.20),
    },
    "timestamp": True,
    "encoding": "z64",
}

# The stored slug is 24 characters AND a genuine truncation: the old encoder
# spelled "&" out as "and", so slugify("Photonics & Lasers Index") gives
# "photonics-lasers-index" while the pre-truncation form was the 26-character
# "photonics-and-lasers-index". resolve_slug must regenerate this one.
PHOTONICS = {
    "slug": "photonics-and-lasers-ind",
    "name": "Photonics & Lasers Index",
    "item_count": 6,
    "weights": {"LSER": 25.0, "PHOT": 25.0, "OPTX": 20.0,
                "BEAM": 15.0, "FIBR": 10.0, "WAVE": 5.0},
    "snapshot": {
        # LSER's claim here and OPTICS_STORAGE's below differ by 0.018 -- the
        # narrowest gap between two baskets competing for one symbol, and the
        # ceiling the share tolerance has to stay well under.
        "LSER": (0.03120, 840.25),
        # PHOT carries the worst drift in the fixture: the order that produced
        # it filled 0.047688, so the claim is off by 0.000112 shares. The
        # tolerance has to absorb this.
        "PHOT": (0.04780, 98.40),
        "OPTX": (0.05070, 410.60), "BEAM": (0.08260, 315.80),
        "FIBR": (0.03000, 520.15), "WAVE": (0.09180, 114.90),
    },
    "timestamp": True,
    "encoding": "z64",
}

# Definition only: every `w` entry carries a weight and nothing else, so there
# are no shares to snapshot even though `t` is present. This basket holds ALFA
# and MEMX, both of which another basket claims, and it must never win either
# -- a basket that claims no shares for a symbol cannot own that symbol's
# orders.
BROAD_SEMIS = {
    "slug": "broad-semis-basket",
    "name": "Broad Semis Basket",
    "item_count": 10,
    "weights": {"ALFA": 22.0, "SEMI": 16.0, "FABX": 14.0, "LITH": 10.0,
                "ETCH": 10.0, "WAFR": 8.0, "MEMX": 7.0, "LOGC": 5.0,
                "ANLG": 5.0, "PKGE": 3.0},
    "snapshot": {},
    "timestamp": True,
    "encoding": "z64",
}

# The stored slug is also exactly 24 characters, but its pre-truncation form
# ("optics-and-storage-index") IS 24 characters whole -- it was never cut
# short, so resolve_slug must keep it.
OPTICS_STORAGE = {
    "slug": "optics-and-storage-index",
    "name": "Optics & Storage Index",
    "item_count": 6,
    "weights": {"MEMX": 20.0, "DISK": 20.0, "TAPE": 20.0,
                "FLSH": 15.0, "CTRL": 15.0, "LSER": 10.0},
    "snapshot": {
        "MEMX": (0.02090, 995.30), "DISK": (0.03690, 564.10),
        "TAPE": (0.02250, 925.40), "FLSH": (0.00960, 1635.75),
        "CTRL": (0.07460, 209.85),
        # 0.018 below PHOTONICS' LSER claim; see the note there.
        "LSER": (0.01320, 840.60),
    },
    "timestamp": True,
    "encoding": "z64",
}

# Twelve weights of 10 sum to 120, so normalization has to scale the whole set
# down. Carried as bare JSON rather than a Z64 blob, which is the other
# metadata shape decode_watchlist_metadata accepts. `item_count` is 0 and
# there is no `t`, so its shares never become a snapshot.
SUM_120 = {
    "slug": "sum-120-test",
    "name": "Sum 120 Test Basket",
    "item_count": 0,
    "weights": dict(("TS%02d" % i, 10.0) for i in range(1, 13)),
    "snapshot": dict(("TS%02d" % i, (1.0, 100.0 * i)) for i in range(1, 13)),
    "timestamp": False,
    "encoding": "json",
}

# A short stored slug that bears no resemblance to its display name. It is not
# 24 characters, so it is never a truncation candidate and must be kept as-is.
# `item_count` is 0 here too. None of its twenty symbols has an order.
GRID_20 = {
    "slug": "grid-20",
    "name": "Twenty Symbol Compressed Basket",
    "item_count": 0,
    "weights": dict(("GX%02d" % i, 5.0) for i in range(1, 21)),
    "snapshot": dict(("GX%02d" % i, (0.01500, 152.75)) for i in range(1, 21)),
    "timestamp": True,
    "encoding": "z64",
}

BASKETS = [CORE_GROWTH, PHOTONICS, BROAD_SEMIS, OPTICS_STORAGE, SUM_120, GRID_20]

# --- the three ordinary lists ------------------------------------------------
#
# One with no description at all, one with a description that is plain prose,
# and one that is empty. None of them may decode as basket metadata.
PLAIN_LISTS = [
    {"id": "11111111-1111-4111-8111-111111111111",
     "display_name": "Long Term Watch", "icon_emoji": "⚡️",
     "item_count": 41},
    {"id": "22222222-2222-4222-8222-222222222222",
     "display_name": "Crypto Watch", "icon_emoji": "👾",
     "item_count": 9,
     "display_description": "Coins worth keeping an eye on."},
    {"id": "33333333-3333-4333-8333-333333333333",
     "display_name": "Options Ideas", "icon_emoji": "💡",
     "item_count": 0},
]

BASKET_IDS = [
    "44444444-4444-4444-8444-444444444444",
    "55555555-5555-4555-8555-555555555555",
    "66666666-6666-4666-8666-666666666666",
    "77777777-7777-4777-8777-777777777777",
    "88888888-8888-4888-8888-888888888888",
    "99999999-9999-4999-8999-999999999999",
]


def basket_description(basket):
    """Build one watchlist's `display_description` from a basket definition."""
    weights = {}
    for symbol, weight in basket["weights"].items():
        claim = basket["snapshot"].get(symbol)
        if claim is None:
            weights[symbol] = [weight]
        else:
            weights[symbol] = [weight, claim[0], claim[1]]

    payload = {"s": basket["slug"], "w": weights}
    if basket["timestamp"]:
        payload["t"] = SNAPSHOT_EPOCH

    if basket["encoding"] == "z64":
        return encode_z64(payload)
    return json.dumps(payload, separators=(",", ":"))


def build_watchlists():
    """Return the full synthetic `get_watchlists` response."""
    results = []
    for entry in PLAIN_LISTS:
        row = dict(entry)
        row["owner_type"] = "custom"
        row["allowed_object_types"] = list(ALLOWED_OBJECT_TYPES)
        results.append(row)

    for identifier, basket in zip(BASKET_IDS, BASKETS):
        results.append({
            "id": identifier,
            "display_name": "Basket: %s" % basket["name"],
            "icon_emoji": "🧺",
            "owner_type": "custom",
            "item_count": basket["item_count"],
            "allowed_object_types": list(ALLOWED_OBJECT_TYPES),
            "display_description": basket_description(basket),
        })
    return {"data": {"watchlists": results}}


# --- the orders --------------------------------------------------------------
#
# One filled order per snapshot claim, plus one stray that matches nothing.
# Each quantity sits a few millionths of a share off the claim it answers:
# the claim is a lossy 5-decimal copy, and the order is the authority. The
# fractional-second digits vary from two to six on purpose -- the real
# response was equally inconsistent, and the timestamp parser has to cope.
#
# (order_id, symbol, shares, average_price, fill_time)
ORDERS = [
    # The stray. Bought outside any basket, six days after the bursts below,
    # and no basket's claim comes near it: ALFA's only claim is 0.07120.
    ("aa000001-0000-4000-8000-000000000001", "ALFA", "0.052140", "212.480000",
     "2026-03-18T13:30:02.517Z"),

    # Optics & Storage, one burst.
    ("bb000001-0000-4000-8000-000000000001", "LSER", "0.013184", "840.604100",
     "2026-03-12T17:58:41.004Z"),
    ("bb000002-0000-4000-8000-000000000002", "CTRL", "0.074602", "209.851200",
     "2026-03-12T17:58:38.83Z"),
    ("bb000003-0000-4000-8000-000000000003", "FLSH", "0.009603", "1635.742600",
     "2026-03-12T17:58:36.812Z"),
    ("bb000004-0000-4000-8000-000000000004", "TAPE", "0.022497", "925.418300",
     "2026-03-12T17:58:34.79Z"),
    ("bb000005-0000-4000-8000-000000000005", "DISK", "0.036904", "564.093700",
     "2026-03-12T17:58:32.6Z"),
    ("bb000006-0000-4000-8000-000000000006", "MEMX", "0.020896", "995.312400",
     "2026-03-12T17:58:30.755Z"),

    # Photonics & Lasers, one burst.
    ("cc000001-0000-4000-8000-000000000001", "WAVE", "0.091803", "114.903300",
     "2026-03-12T17:56:22.418Z"),
    ("cc000002-0000-4000-8000-000000000002", "FIBR", "0.029998", "520.147900",
     "2026-03-12T17:56:20.331Z"),
    ("cc000003-0000-4000-8000-000000000003", "BEAM", "0.082605", "315.786200",
     "2026-03-12T17:56:18.24Z"),
    ("cc000004-0000-4000-8000-000000000004", "OPTX", "0.050694", "410.612800",
     "2026-03-12T17:56:16.109Z"),
    # The worst drift in the fixture: 0.000112 shares off the 0.04780 claim.
    ("cc000005-0000-4000-8000-000000000005", "PHOT", "0.047688", "98.417500",
     "2026-03-12T17:56:14.62Z"),
    ("cc000006-0000-4000-8000-000000000006", "LSER", "0.031206", "840.238600",
     "2026-03-12T17:56:12.937Z"),

    # Core Growth, one burst.
    ("dd000001-0000-4000-8000-000000000001", "IOTA", "0.127296", "118.204700",
     "2026-03-12T17:54:35.842Z"),
    ("dd000002-0000-4000-8000-000000000002", "ZETA", "0.024403", "612.751600",
     "2026-03-12T17:54:33.716Z"),
    ("dd000003-0000-4000-8000-000000000003", "EPSI", "0.063395", "236.806300",
     "2026-03-12T17:54:31.508Z"),
    ("dd000004-0000-4000-8000-000000000004", "DLTA", "0.047102", "320.108800",
     "2026-03-12T17:54:29.437Z"),
    ("dd000005-0000-4000-8000-000000000005", "GAMA", "0.046307", "325.402900",
     "2026-03-12T17:54:27.22Z"),
    ("dd000006-0000-4000-8000-000000000006", "BETA", "0.039096", "385.196400",
     "2026-03-12T17:54:25.114Z"),
    ("dd000007-0000-4000-8000-000000000007", "ALFA", "0.071204", "210.550100",
     "2026-03-12T17:54:23.061Z"),
]


def build_orders():
    """Return the full synthetic `get_equity_orders` response."""
    results = []
    for order_id, symbol, shares, average_price, fill_time in ORDERS:
        # `price` is the limit/reference price. It is deliberately a different
        # number from `average_price`, because migrate_v2 must read the fill
        # price and never this one.
        reference = "%.6f" % (float(average_price) - 1.0)
        results.append({
            "id": order_id,
            "symbol": symbol,
            "side": "buy",
            "type": "market",
            "state": "filled",
            "quantity": shares,
            "cumulative_quantity": shares,
            "price": reference,
            "average_price": average_price,
            "last_transaction_at": fill_time,
            "created_at": fill_time,
            "placed_agent": "agentic",
            "executions": [{
                "price": average_price,
                "quantity": shares,
                "timestamp": fill_time,
            }],
        })
    return {"data": {"orders": results}}


def main():
    for name, payload in (("sample_watchlists.json", build_watchlists()),
                          ("sample_orders.json", build_orders())):
        path = HERE / name
        path.write_text(json.dumps(payload, indent=1) + "\n")
        print("wrote %s" % path)


if __name__ == "__main__":
    main()
