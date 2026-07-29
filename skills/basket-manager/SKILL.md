---
name: basket-manager
description: >
  Create and manage custom stock baskets (user-defined indices with target allocations and
  position tracking). Baskets live in a local event-log store at ~/.tradethos, not in
  Robinhood. Supports creating, viewing, editing, and deleting basket definitions, and tracks
  actual positions with a share ledger, average cost, and realized profit and loss. Use this
  skill when the user wants to build a custom basket of stocks, set target weights, track a
  model basket, or manage their investment thesis for a group of holdings.
---

# Basket Manager

Manage custom stock baskets. A basket is a named collection of stocks with target percentage
weights, an investment thesis per holding, and a real position ledger. The ledger tracks what
the user actually bought for that basket: shares, average cost, and realized profit and loss.

All basket data lives on the local machine, in `~/.tradethos`. No basket operation needs the
network. A basket's positions are separate from the user's full brokerage account, so the same
stock can sit in two baskets, or sit in a basket and outside every basket, without conflict.

## When to Use This Skill

Trigger this skill when the user mentions:
- Creating a basket, index, or collection of stocks
- Adding or removing stocks from a basket
- Viewing or listing their custom baskets
- Setting target weights or allocations
- Comparing baskets
- Recording a transaction in a basket
- "My baskets" or "my indices"

## General Rules & Standards

- **Formatting**: Present monetary values with proper formatting (e.g., `$1,234.56`). Present percentages to two decimal places (e.g., `12.34%`).
- **Stock Data**: Always include the symbol, current price, and relevant context (e.g., day change) when presenting basket stock metrics.
- **User Confirmation**: Confirm before deleting a basket, removing a holding with a position, or writing a weight change.
- **Cross-Skill Offers**: Frame suggestions to research or trade basket holdings as optional offers, never automatic actions.
- **The agent never edits a basket file.** `basket.py` is the only writer. It appends to the
  event log and then exports a snapshot. No other code path may touch `events.log.jsonl` or a
  file under `baskets/`.

## How Baskets Are Stored

The store is one append-only log, plus one snapshot file per basket.

```
~/.tradethos/
  events.log.jsonl              Every event. Append only. The source of truth.
  baskets/
    <slug>.json                 A snapshot. An export for the user. No command reads it.
  backup.marker                 The event count and time of the last backup.
  backups/
    events-<timestamp>.jsonl    Timestamped copies, written automatically and by `backup`.
```

Three rules follow from this layout.

- **The event log is the only fact.** A basket's name, its target weights, its thesis text,
  and its trade history are all events in the log. `basket.py` computes every basket by
  replaying the log from the start, every time it reads.
- **A snapshot is an export, not a source.** `basket.py show`, `list`, and `history` never
  open a snapshot file. A missing or corrupt snapshot changes nothing; `export` writes it
  again.
- **No command takes a typed share count or a typed price.** The only way a trade enters the
  log is `record-fills`, reading the fill from a real Robinhood order. Section "Recording
  Trades" below gives the rule in full.

## Locating the Tool

`basket.py` lives in the `scripts/` directory next to this SKILL.md. Resolve the path for the
current environment before running anything — do not assume the working directory is a
Tradethos checkout:

- **Claude Code (installed plugin)**: `${CLAUDE_PLUGIN_ROOT}/skills/basket-manager/scripts/`
- **Local checkout**: `skills/basket-manager/scripts/` from the repo root

The examples below use `$SCRIPTS` for that directory, and `$BASKET` for
`python3 "$SCRIPTS/basket.py"`.

Every command defaults to the real store at `~/.tradethos`. **Do not pass `--data-dir`** in
normal use. Pass `--data-dir` only inside a test, pointed at a temporary directory.

Every command prints JSON on stdout by default. Pass `--format table` for a short
human-readable view; `list` supports it, and every command still accepts it (most commands,
including `history`, fall back to JSON when they have no table view of their own).

## Commands

### `create` — make a new basket

```bash
$BASKET create "Storage Leaders" \
  --symbols WDC:50,STX:50 \
  --account 000000000 \
  --description "Enterprise storage and memory" \
  --threshold 5.0
```

- `--symbols` is `SYMBOL:WEIGHT,SYMBOL:WEIGHT,...`. A weight is a ratio or a percent; the tool
  normalizes it to whole numbers that sum to 100.
- `--account` is required. It is the brokerage account that will hold this basket's trades.
  `record-fills` later refuses an order from any other account.
- `--description` and `--threshold` are optional. `--threshold` is the rebalance drift alert
  percent, default 5.0.
- The slug comes from the name (lowercase, hyphens for spaces) and never changes afterward,
  even if the name changes later with `set-name`.
- The reply's `normalized` key lists every symbol whose weight the tool changed to make the
  set sum to 100. Show this to the user.

### `list` — every basket

```bash
$BASKET list
$BASKET list --format table
```

Returns each basket's slug, name, holding count, total invested, and realized profit and loss.
No prices, no per-holding detail — use `show` for one basket.

### `show` — one basket

```bash
$BASKET show storage-leaders
$BASKET show storage-leaders --prices '{"WDC":60.00,"STX":92.00}'
```

Returns every holding: symbol, target weight, thesis, and position (shares, average cost,
total invested, realized profit and loss — `null` when the basket has never held the symbol).
With `--prices`, it also returns each holding's current value and unrealized profit or loss,
and the basket's total current value. Fetch prices with `get_equity_quotes` first; the tool
does no pricing itself.

### `history` — the events of a basket

```bash
$BASKET history storage-leaders
$BASKET history storage-leaders --symbol WDC
$BASKET history --since 2026-07-01
```

Prints the raw events in date order: every `basket_created`, `holding_added`,
`weight_changed`, `buy`, `sell`, and so on. Omit the slug to see every basket's history at
once. This is how the user answers "when did I raise NVDA's target to 20 percent" — the
answer is a `weight_changed` event, not a note anyone had to keep by hand.

### `export` — write the snapshot files

```bash
$BASKET export storage-leaders
$BASKET export
```

Rebuilds the snapshot file for one basket, or for every basket with no slug. No command reads
these files back; they exist so the user has a readable copy to open or copy. Run this after
deleting or damaging the `baskets/` directory to restore it — nothing is lost, because the log
holds every fact.

### Changing target weights

**A target weight is a whole percent. A basket's weights always sum to exactly 100.**
`basket.py` enforces this after every command; it normalizes and reports every weight it
changed. A change to one holding always changes the others, because the total cannot move.

```bash
# Set one holding's weight; the tool redistributes the rest
$BASKET set-weight storage-leaders --symbol WDC --weight 60 --dry-run
$BASKET set-weight storage-leaders --symbol WDC --weight 60 --fill equal

# Set every holding's weight in one call — no redistribution, an exact set
$BASKET set-weights storage-leaders --weights WDC:60,STX:40 --dry-run
$BASKET set-weights storage-leaders --weights WDC:60,STX:40

# Add or remove a holding — the other weights scale to make room
$BASKET add-holding storage-leaders --symbol MU --weight 20 --thesis "DRAM and NAND leader"
$BASKET remove-holding storage-leaders --symbol MU
$BASKET remove-holding storage-leaders --symbol MU --force   # even if it still holds shares
```

- `--fill proportional` (the default) keeps the other holdings' ratios to each other.
  `--fill equal` gives every other holding the same weight. `set-weight`, `add-holding`, and
  `remove-holding` all take `--fill`.
- `--dry-run` prints the complete weight set the command would write, and appends nothing.
  **Always run a weight change with `--dry-run` first**, show the full resulting set — not
  just the holding the user named — and get the user's confirmation before writing it for
  real. See "The Weight Change Loop" below for the full pattern.
- `set-weights` needs a weight for every current holding; it refuses a list that omits one, or
  that names a symbol the basket does not hold.
- `remove-holding` refuses to remove a holding that still has shares, unless `--force` is
  given, so a removal never silently drops a real position from the record. The same guard
  covers a holding with realized profit or loss but no current shares.

#### The Weight Change Loop

The user says "raise WDC to 60 percent." This loop is how the agent must handle any weight
change, per the design's safety rule: never write a weight change the user has not seen in
its final, normalized form.

1. Run `set-weight storage-leaders --symbol WDC --weight 60 --dry-run`.
2. Show every holding's old and new weight — including the ones the user did not name.
3. State the fill mode used, and offer the other mode.
4. Get the user's confirmation, or different numbers.
5. If the user gives different numbers, return to step 1 with `set-weights --dry-run` and
   those numbers — the tool will normalize them, because a user's numbers rarely sum to
   exactly 100. Show the normalized set and ask again.
6. Once the user approves a set the tool did not change, write it with `set-weights` (not
   `set-weight`), passing the exact confirmed set. `set-weights` performs no arithmetic of its
   own, so what gets written matches exactly what the user saw.

### `set-thesis`, `set-name`, `set-description`, `set-threshold`

```bash
$BASKET set-thesis storage-leaders --symbol WDC --thesis "Enterprise HDD and NAND leader"
$BASKET set-name storage-leaders --name "Storage & Memory Leaders"
$BASKET set-description storage-leaders --description "Data storage infrastructure"
$BASKET set-threshold storage-leaders --threshold 3.0
```

`set-name` changes the display name only. **The slug never changes**, because every event in
the log refers to the basket by slug.

### `delete` — remove a basket

```bash
$BASKET delete storage-leaders
$BASKET delete storage-leaders --force
```

Refuses when the basket still holds shares, or still carries realized profit or loss, unless
`--force` is given. **Deletion removes the record only. It does not sell any stock.** State
this to the user before using `--force` on a basket with a position — the shares stay in the
brokerage account with no basket tracking them anymore.

### `plan-buy` — plan a whole-basket purchase

```bash
$BASKET plan-buy storage-leaders --amount 500
$BASKET plan-buy storage-leaders --amount 500 --prices '{"WDC":50.00,"STX":92.00}'
```

Divides the dollar amount across the target weights and returns a dollar allocation per
symbol. With `--prices`, it also returns an estimated share count per symbol. `plan-buy`
writes nothing — it is a calculator, not a gate. The agent uses its output to place real
orders (see "Recording Trades" below), never to compute the split by hand.

### `plan-sell` — plan a whole-basket sale

```bash
$BASKET plan-sell storage-leaders --all
$BASKET plan-sell storage-leaders --all --prices '{"WDC":60.00,"STX":92.00}'
$BASKET plan-sell storage-leaders --amount 200 --prices '{"WDC":60.00,"STX":92.00}'
```

`--all` returns every held symbol's full share count. This plan exits the basket. `--amount`
divides the dollar amount by each holding's **current market value**, so the sale keeps the
basket's current weights unchanged. It needs `--prices`. It refuses an amount above the
basket's current value, and names both numbers and suggests `--all` instead. Rebalancing a
basket toward its target weights is a different operation, and it is out of scope for this
tool. `calc_drift.py` reports the gap. A rebalance today is a set of ordinary buys and sells
that the agent plans by hand.

### Recording Trades

**`record-fills` is the only command that writes a trade, and it reads every number from a
real Robinhood order.** No command anywhere in this tool takes a typed share count or a typed
price.

```bash
$BASKET record-fills storage-leaders \
  --orders-json '<the get_equity_orders response>' \
  --order-ids REDACTEDA1-0000-0000-0000-000000000001 \
  --account 000000000
```

- `--orders-json` is the raw `get_equity_orders` response, unmodified.
- `--order-ids` is a comma-separated list of the order ids to record. The tool reads the
  symbol, the filled quantity, the fill price, and the side from each named order in the
  response, and appends one `buy` or `sell` event per order.
- `--account` must match the basket's own account, or the tool refuses the whole call.
- The tool reads `average_price` from the order, never `price` — `price` is the limit or
  reference price, and it can differ from what the order actually filled at.

**`--order-ids` is a claim the tool cannot check.** It records whatever orders the list names.
It does not verify that those shares were meant for the named basket. **Pass only the ids of
orders the agent itself placed for this basket, in this same conversation.** Never pass an id
by guessing from the symbol alone. The user may hold that stock outside every basket, or
inside a different basket. A wrong id silently moves a real trade onto the wrong ledger.

**One order funds one basket only.** If an id already belongs to another basket, the tool
skips that order and reports it, instead of recording it. Tell the user before placing an
order for a stock that another basket already claims. The user then places a second, separate
order if the new basket should hold its own shares.

A single order id is a valid call — buying one stock for one basket is `record-fills` with one
id, the same command as recording a batch of seven.

**A repeated call is safe.** An order id already recorded in the same basket changes nothing
and reports `already_recorded`; retry a failed or uncertain call freely. A limit order that is
still open when `get_equity_orders` is first called just needs the same `record-fills` call
again later, once it fills.

**A batch records what it can.** `record-fills` applies each order id on its own. It records
every order that passes, and it skips and reports every order that fails, with a reason
(`ORDER_NOT_IN_RESPONSE`, `NOT_FILLED`, `ORDER_IN_OTHER_BASKET`, `SYMBOL_NOT_IN_BASKET`,
`OVERSELL`, and so on). Exit code 0 means the log is now correct, not that every id succeeded
— **read the `skipped` list and tell the user about every entry in it.** Exit code 1 means
nothing was recorded at all.

`--cap-at-held` handles a sell order that mixes basket shares with shares held outside the
basket: instead of skipping the oversized sell, the tool records a sale of exactly the shares
the basket holds, at the order's real fill price, and reports the capped count. State that
count to the user before using it. It only works with exactly one order id.

### `verify` — check the log against the real account

```bash
$BASKET verify
$BASKET verify storage-leaders --positions '<the get_equity_positions response>'
```

With no `--positions`, `verify` checks the log's own integrity: it reports any line the replay
had to skip, any duplicate order id it ignored, and whether the automatic backup is current.

With `--positions`, it also compares every symbol's total claimed shares (summed across every
basket) to the real account position, and reports one of three states per symbol:

| State | Meaning | Agent action |
|---|---|---|
| `match` | The claims equal the account position. | None. |
| `outside_shares` | The claims are below the account position. | None — this is normal. The user holds extra shares outside any basket. |
| `over_claimed` | The claims are above the account position. | The user sold shares outside a basket. Offer the repair below. |

**Run `verify --positions` whenever reporting a basket's holdings**, and only flag
`over_claimed` — an `outside_shares` result needs no warning and no repair.

**The repair for `over_claimed`:** the user sold basket shares outside the basket, so the
record must follow the real sale. Find the sale order in `get_equity_orders`. Show it to the
user: symbol, shares, `average_price`, and date. Get confirmation that it is the sale in
question. Then call `record-fills` with that order id. A sale order can cover more shares than
the basket claims, when it mixed basket shares with outside shares in one order. In that case,
use `--cap-at-held` and state the capped count first. One sale order can repair one basket
only. If two overlapping baskets lost shares in the same order, only one of them can be
repaired from it.

### `backup` — copy the event log

```bash
$BASKET backup
$BASKET backup --to /path/to/directory
```

Copies `events.log.jsonl` to a timestamped file under `~/.tradethos/backups/` (or `--to`), and
updates `backup.marker`. `basket.py` also runs this by itself after any write once the log has
grown by 20 events since the last backup, so this command is rarely needed by hand — `verify`
reports when the marker is stale or missing.

## Other Scripts

Two more scripts live next to `basket.py`, for math that `show` does not do. Both read the
event log directly — the same store, no separate data source — and write nothing. Both take
`--slug <slug>` or `--all`, `--prices '<json>'`, and `--format table`, and both accept
`--data-dir` the same way `basket.py` does.

```bash
python3 "$SCRIPTS/calc_performance.py" --slug storage-leaders --prices '{"WDC":60.00,"STX":92.00}'
python3 "$SCRIPTS/calc_drift.py" --all --prices '{"WDC":60.00,"STX":92.00}' --threshold 3.0
```

- **`calc_performance.py`** adds the profit-and-loss percentage per holding and a total across
  every basket in one call — `show --prices` already gives the dollar value and profit or loss
  per holding, so reach for this only when a percentage or a multi-basket table is needed.
- **`calc_drift.py`** compares each holding's actual weight (by current market value) to its
  target weight, and flags a holding whose drift passes the threshold. `--threshold` overrides
  the basket's own `rebalance_threshold_pct`; with no flag, the basket's own value applies, then
  `config.json`'s `rebalancing.default_threshold_pct`, then 5.0.

## Cross-Skill Integration

- After creating a basket, suggest using the **trade-executor** skill to buy the holdings.
- When viewing a basket, offer to run **stock-researcher** on any holding for deeper analysis.
- Suggest the **portfolio-tracker** skill to compare actual Robinhood positions against basket
  targets, and to run `verify --positions`.
- When the **stock-screener** finds interesting stocks, offer to add them to a basket.
- After a trade fills via **trade-executor**, offer to record it with `record-fills` — only
  when the order id belongs to a basket the agent placed it for.

## Example Interactions

**User**: "Create a basket called Storage Leaders with WDC at 50% and STX at 50%"
→ Confirm the account → `create` with `--symbols WDC:50,STX:50 --account <acct>` → show the
result, including any weight the tool normalized

**User**: "Show me my baskets"
→ `list` → summary table with holding counts and invested amounts

**User**: "How's my Storage Leaders basket doing?"
→ Fetch quotes for its holdings → `show storage-leaders --prices '{...}'` → present per-holding
value and profit or loss → run `verify --positions` and flag any `over_claimed` symbol

**User**: "Raise WDC to 60% in my Storage Leaders basket"
→ `set-weight --symbol WDC --weight 60 --dry-run` → show the full resulting set → confirm →
`set-weights` with the confirmed numbers

**User**: "I just bought 10 shares of WDC for my Storage basket"
→ Confirm the order was placed through trade-executor and its order id is on hand →
`record-fills storage-leaders --order-ids <id> --account <acct> --orders-json '<...>'` → report
the new position

**User**: "Add MU to my Storage Leaders basket at 20%"
→ `add-holding --symbol MU --weight 20 --dry-run` → show the resulting weights for every
holding → confirm → `add-holding` for real
