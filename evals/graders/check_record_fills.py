"""Grade what `record-fills` actually wrote against what the broker filled.

This is the regression test for the 2026-08-03 defect. That day an agent
hand-built the orders JSON instead of forwarding the `get_equity_orders`
response. The share counts and the prices were right, so every report looked
correct. The timestamps were not: all six events carried the moment the
command ran rather than the moment each order filled.

The check that catches it compares each event's `ts` to the
`last_transaction_at` of the order it claims to record. No summary and no
final message can hide a mismatch there, because both numbers come from
outside the agent.

Four assertions, in the order they fail as the failure gets worse:

  1. completeness - every order the agent placed reached the ledger
  2. timestamp    - each event is stamped from the fill, not the wall clock
  3. shares       - the share count came from the order, not from typing
  4. price        - the price came from the fill, not from the quote

Assertion 4 deserves a note. The quote and the fill differ: a buy fills at
the ask. An agent that records the quote produces a number that looks
plausible and is wrong, and only a comparison against `average_price` finds
it.

Output uses the field names `text`, `passed` and `evidence` because the eval
viewer reads those exact keys.
"""

import argparse
import json


TOLERANCE = 1e-9


def load_events(path):
    with open(path) as handle:
        return [json.loads(line) for line in handle if line.strip()]


def index_orders(orders_response):
    """Map order id to order, from a raw `get_equity_orders` response."""
    orders = (orders_response.get("data") or {}).get("orders") or []
    return dict((o["id"], o) for o in orders if o.get("id"))


def grade(events, orders_by_id, expected_ids, slug=None):
    trades = [e for e in events
              if e.get("type") in ("buy", "sell")
              and (slug is None or e.get("slug") == slug)]
    recorded = dict((e["order_id"], e) for e in trades if e.get("order_id"))

    results = []

    missing = [i for i in expected_ids if i not in recorded]
    results.append({
        "text": "Every placed order reached the basket ledger",
        "passed": not missing,
        "evidence": ("all %d recorded" % len(expected_ids)) if not missing
                    else "not recorded: %s" % ", ".join(missing)})

    # The remaining three only mean something for orders that were recorded.
    # Reporting them as failures too would triple-count a single miss.
    present = [i for i in expected_ids if i in recorded]

    bad_ts = []
    for order_id in present:
        event = recorded[order_id]
        order = orders_by_id.get(order_id) or {}
        expected = order.get("last_transaction_at")
        if event.get("ts") != expected:
            bad_ts.append("%s: recorded %s, filled %s"
                          % (order_id[:8], event.get("ts"), expected))
    results.append({
        "text": "Each event is stamped with the real fill time, "
                "not the time the command ran",
        "passed": not bad_ts,
        "evidence": ("%d event(s) match last_transaction_at" % len(present))
                    if not bad_ts else "; ".join(bad_ts)})

    bad_shares = []
    for order_id in present:
        event = recorded[order_id]
        order = orders_by_id.get(order_id) or {}
        expected = float(order.get("cumulative_quantity") or 0)
        if abs(float(event.get("shares") or 0) - expected) > TOLERANCE:
            bad_shares.append("%s: recorded %s, filled %s"
                              % (order_id[:8], event.get("shares"), expected))
    results.append({
        "text": "Share counts come from the order, not from a typed number",
        "passed": not bad_shares,
        "evidence": ("%d share count(s) match cumulative_quantity"
                     % len(present)) if not bad_shares
                    else "; ".join(bad_shares)})

    bad_price = []
    for order_id in present:
        event = recorded[order_id]
        order = orders_by_id.get(order_id) or {}
        expected = float(order.get("average_price") or 0)
        if abs(float(event.get("price") or 0) - expected) > TOLERANCE:
            bad_price.append("%s: recorded %s, filled %s"
                             % (order_id[:8], event.get("price"), expected))
    results.append({
        "text": "Fill prices come from average_price, not from the quote",
        "passed": not bad_price,
        "evidence": ("%d price(s) match average_price" % len(present))
                    if not bad_price else "; ".join(bad_price)})

    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", required=True,
                        help="Path to events.log.jsonl")
    parser.add_argument("--orders", required=True,
                        help="JSON file holding a get_equity_orders response")
    parser.add_argument("--order-ids", required=True,
                        help="Comma-separated ids the agent should have "
                             "recorded")
    parser.add_argument("--slug", help="Restrict to one basket")
    parser.add_argument("--out", help="Write grading JSON here")
    args = parser.parse_args(argv)

    with open(args.orders) as handle:
        orders_by_id = index_orders(json.load(handle))
    expected = [i.strip() for i in args.order_ids.split(",") if i.strip()]

    results = grade(load_events(args.log), orders_by_id, expected, args.slug)
    payload = {"expectations": results,
               "passed": all(r["passed"] for r in results)}

    text = json.dumps(payload, indent=2)
    if args.out:
        with open(args.out, "w") as handle:
            handle.write(text + "\n")
    print(text)
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
