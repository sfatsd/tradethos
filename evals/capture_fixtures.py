"""Turn real MCP responses into fixtures and into a seed for the fake broker.

The MCP tools belong to the agent, not to a subprocess, so this script cannot
call Robinhood itself. The flow is that the agent makes the call and pipes the
response here:

    # agent calls get_equity_quotes, then:
    python -m evals.capture_fixtures --tool get_equity_quotes < quotes.json

Each capture is written to `evals/fixtures/<tool>.json` with the account
numbers replaced. A fixture is committed, so a real account number in one
would leak the moment the repository is shared.

Once quotes and positions are captured, build a seed for the fake broker:

    python -m evals.capture_fixtures --make-seed

The seed matters more than the raw fixtures. It carries the prices and the
holdings that the fake answers with, so an eval runs against numbers the
market actually produced rather than numbers someone invented. Invented
numbers hide bugs: a made-up quote where the bid, the ask and the last trade
are all equal makes it impossible to tell a fill price from a quote price,
and telling those apart is one of the four checks this suite runs.

**Fixtures go stale.** The captured shape is true on the day it was captured.
Re-capture before a release and read the diff. A change in shape is itself
worth knowing about, so do not overwrite without looking.
"""

import argparse
import json
import re
import sys
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# The fake uses these two, so real numbers map onto them and every eval keeps
# working against a capture from any account.
AGENTIC_PLACEHOLDER = "123456789"
NON_AGENTIC_PLACEHOLDER = "9XY87654"

ACCOUNT_KEYS = ("account_number", "rhs_account_number", "rhc_account_number")


def mask_accounts(node, mapping):
    """Replace every account number, wherever it appears in the payload.

    A blanket string substitution would also hit order ids and instrument
    ids, which share the same alphabet. Walking the structure and rewriting
    only the known account keys keeps the rest of the capture intact.
    """
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key in ACCOUNT_KEYS and isinstance(value, str):
                out[key] = mapping.setdefault(
                    value,
                    AGENTIC_PLACEHOLDER if len(mapping) == 0
                    else NON_AGENTIC_PLACEHOLDER)
            else:
                out[key] = mask_accounts(value, mapping)
        return out
    if isinstance(node, list):
        return [mask_accounts(item, mapping) for item in node]
    return node


def account_values(node, found=None):
    """Collect every value stored under an account key."""
    if found is None:
        found = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ACCOUNT_KEYS and isinstance(value, str):
                found.add(value)
            else:
                account_values(value, found)
    elif isinstance(node, list):
        for item in node:
            account_values(item, found)
    return found


def verify_masked(original, masked):
    """Fail when a real account number survived the substitution.

    Masking is only ever as good as the placeholder it substitutes in. The
    first time this script ran, `AGENTIC_PLACEHOLDER` happened to hold a
    real account number, so the substitution mapped that number to itself.
    The capture was masked in form and unmasked in fact, and nothing said
    so: the code did exactly what it claimed and the output was still
    wrong.

    Comparing the values before and after is what catches that. It also
    catches any future placeholder that collides with real data, which is
    the same bug wearing different digits.
    """
    survivors = account_values(original) & account_values(masked)
    if survivors:
        raise SystemExit(
            "Masking did not change %d account value(s): %s\n"
            "A placeholder in capture_fixtures.py matches a real account "
            "number, so the substitution mapped it to itself. Change the "
            "placeholder before capturing again."
            % (len(survivors), ", ".join(sorted(survivors))))


def capture(tool, payload):
    FIXTURES.mkdir(parents=True, exist_ok=True)
    masked = mask_accounts(payload, {})
    verify_masked(payload, masked)
    path = FIXTURES / ("%s.json" % tool)
    if path.exists():
        print("note: %s already exists. Read the diff before you keep it."
              % path.name, file=sys.stderr)
    with open(path, "w") as handle:
        handle.write(json.dumps(masked, indent=2) + "\n")
    return path


def _read(tool):
    path = FIXTURES / ("%s.json" % tool)
    if not path.exists():
        return None
    with open(path) as handle:
        return json.load(handle)


def make_seed():
    """Build Broker constructor arguments from the captured fixtures."""
    seed = {}

    quotes_payload = _read("get_equity_quotes")
    if quotes_payload:
        quotes = {}
        for row in (quotes_payload.get("data") or {}).get("results") or []:
            quote = row.get("quote") or {}
            symbol = quote.get("symbol")
            if not symbol:
                continue
            quotes[symbol] = {
                "last": float(quote["last_trade_price"]),
                "prev": float(quote["adjusted_previous_close"]),
                "bid": float(quote["bid_price"]),
                "ask": float(quote["ask_price"])}
        if quotes:
            seed["quotes"] = quotes

    positions_payload = _read("get_equity_positions")
    if positions_payload:
        positions = {}
        for row in (positions_payload.get("data") or {}).get("positions") or []:
            positions[row["symbol"]] = {
                "quantity": float(row["quantity"]),
                "average_buy_price": float(row["average_buy_price"])}
        if positions:
            seed["positions"] = positions

    portfolio_payload = _read("get_portfolio")
    if portfolio_payload:
        data = portfolio_payload.get("data") or {}
        if data.get("cash") is not None:
            seed["cash"] = float(data["cash"])

    if not seed:
        raise SystemExit(
            "No fixtures found. Capture get_equity_quotes and "
            "get_equity_positions first.")

    path = FIXTURES / "seed.json"
    with open(path, "w") as handle:
        handle.write(json.dumps(seed, indent=2) + "\n")
    return path, seed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tool", help="Tool name this response came from")
    parser.add_argument("--make-seed", action="store_true",
                        help="Build seed.json from the captured fixtures")
    args = parser.parse_args(argv)

    if args.make_seed:
        path, seed = make_seed()
        print("wrote %s" % path)
        print("  quotes:    %d" % len(seed.get("quotes", {})))
        print("  positions: %d" % len(seed.get("positions", {})))
        if "cash" in seed:
            print("  cash:      %.2f" % seed["cash"])
        return 0

    if not args.tool:
        parser.error("give --tool NAME, or --make-seed")
    if not re.match(r"^[a-z_]+$", args.tool):
        parser.error("tool names are lowercase with underscores")

    payload = json.load(sys.stdin)
    print("wrote %s" % capture(args.tool, payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
