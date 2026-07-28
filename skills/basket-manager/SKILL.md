---
name: basket-manager
description: >
  Create and manage custom stock baskets (user-defined indices with target allocations and
  position tracking). Supports creating, viewing, editing, and deleting basket definitions
  stored cloud-natively as Robinhood Watchlists. Tracks actual positions with share counts,
  average cost, and performance metrics. Use this skill when the user wants to build a custom
  basket of stocks, set target weights, track a model basket, or manage their investment thesis
  for a group of holdings.
---

# Basket Manager

Manage custom stock baskets — user-defined indices with target percentage weights, investment theses, and actual position tracking. Each basket is a named collection of stocks that tracks both what you *want* to own (target weights) and what you *actually* own (transactions, shares, avg cost).

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
- **User Confirmation**: Confirm before deleting a basket or overwriting existing basket files.
- **Cross-Skill Offers**: Frame suggestions to research or trade basket holdings as optional offers, never automatic actions.
- **Basket-Level Transactions**: Only trades executed specifically for a custom basket (e.g., "invest $1,000 in my AI basket" or explicit basket rebalancing) are recorded in that basket's transaction history. General brokerage trades outside a basket are not automatically added, so stocks present in multiple baskets never conflict.

## Basket Storage

Baskets are stored cloud-natively as Robinhood Watchlists:
- **Cloud-Native Robinhood Watchlists**:
   - Stored directly on Robinhood using `create_watchlist`, `update_watchlist`, and `get_watchlists`.
   - **Watchlist Name**: `Basket: <Display Name>` (e.g. `Basket: Storage Leaders`).
   - **Metadata Description**: Target weights, threshold, and baseline snapshots encoded in `display_description` using `zlib` Base64 compression (`Z64:...`) via `basket_utils.py`, under Robinhood's 256-character limit.
   - **Capacity**: weights alone compress to 30+ symbols, but each holding in the baseline snapshot costs ~20 more characters. A 7-symbol basket with a full snapshot measures **240 of 256**. Treat ~10 positioned symbols as the practical ceiling, and warn the user before adding holdings to a basket that is already near it — `encode_watchlist_metadata` raises `ValueError` rather than emitting a payload Robinhood would reject.
   - **Position Tracking (Primary)**: Holdings, cost basis, and total invested amounts are read from the baseline snapshot stored in `Z64:` metadata. Baseline snapshots are updated in-place after trade fills via `update_basket_watchlist_baseline` so metadata length never grows.
   - **Position Recovery (Fallback)**: If a baseline snapshot update fails or data appears inconsistent, `reconstruct_basket_positions(orders, basket_symbols, snapshot)` replays filled orders from `get_equity_orders` after the snapshot timestamp to rebuild accurate positions. Always pass the basket's snapshot to avoid double-counting stocks that appear in multiple baskets.

## Basket JSON Schema (local files — legacy & migration only)

This is the **local file format**, not the cloud format. It is still read by the utility
scripts via `--basket <slug>` and by `migrate_to_watchlists.py`, but new baskets are created
as Watchlists (see *Basket Storage* above). Cloud baskets store a compact subset — target
weights, threshold, and a `shares`/`avg_cost` snapshot — and do **not** carry per-transaction
history or theses.

Local basket files live in `data/baskets/<slug>.json` and follow this structure:

```json
{
  "name": "Storage & Memory Index",
  "description": "Custom basket of leading enterprise data storage leaders",
  "created_at": "2026-07-22T15:00:00Z",
  "updated_at": "2026-07-22T22:30:00Z",
  "total_invested": 5000.00,
  "rebalance_threshold_pct": 5.0,
  "holdings": [
    {
      "symbol": "WDC",
      "target_weight_pct": 20.0,
      "thesis": "Enterprise capacity HDD & NAND flash storage leader",
      "position": {
        "shares": 12.658,
        "avg_cost": 79.00,
        "total_invested": 1000.00,
        "transactions": [
          {
            "date": "2026-07-22T15:30:00Z",
            "action": "buy",
            "shares": 12.658,
            "price": 79.00,
            "amount": 1000.00,
            "note": "Initial position"
          }
        ]
      }
    },
    {
      "symbol": "MU",
      "target_weight_pct": 20.0,
      "thesis": "DRAM, NAND Flash, and HBM semiconductor leader",
      "position": null
    }
  ],
  "notes": "Basket covering key hardware layers of data storage infrastructure."
}
```

### Schema Fields

#### Basket Level
| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | Yes | Human-readable basket name |
| `description` | string | Yes | Investment thesis or purpose |
| `created_at` | string | Yes | ISO 8601 timestamp of creation |
| `updated_at` | string | Yes | ISO 8601 timestamp of last modification |
| `total_invested` | number | Yes | Sum of all holdings' total_invested (dollars) |
| `rebalance_threshold_pct` | number | No | Drift threshold % to trigger rebalance alerts (default: 5.0%) |
| `holdings` | array | Yes | List of holdings |
| `notes` | string | No | General basket notes |

#### Holding Level
| Field | Type | Required | Description |
|---|---|---|---|
| `symbol` | string | Yes | Stock ticker symbol (uppercase) |
| `target_weight_pct` | number | Yes | Target allocation percentage (0-100) |
| `thesis` | string | No | Why this stock is in the basket |
| `position` | object/null | Yes | Actual position data, or `null` if no position yet |

#### Position Level (when not null)
| Field | Type | Required | Description |
|---|---|---|---|
| `shares` | number | Yes | Current share count held |
| `avg_cost` | number | Yes | Average cost per share (weighted) |
| `total_invested` | number | Yes | Net dollars invested in this holding |
| `transactions` | array | Yes | Full buy/sell history |

#### Transaction Level
| Field | Type | Required | Description |
|---|---|---|---|
| `date` | string | Yes | ISO 8601 timestamp |
| `action` | string | Yes | `"buy"` or `"sell"` |
| `shares` | number | Yes | Shares in this transaction |
| `price` | number | Yes | Price per share at execution |
| `amount` | number | Yes | Total dollar amount (shares × price) |
| `note` | string | No | Optional note (e.g., "Initial position", "Rebalancing") |

### Weight Validation
- All `target_weight_pct` values must be between 0 and 100
- Weights should ideally sum to 100%, but under-allocation is allowed (remainder = implicit cash)
- Weights summing to more than 100% should be flagged as an error

## Average Cost Calculation

### On Buy
```
new_total_invested = old_total_invested + buy_amount
new_shares = old_shares + buy_shares
new_avg_cost = new_total_invested / new_shares
```

### On Sell
```
realized_pnl = (sell_price - avg_cost) × shares_sold
new_total_invested = old_total_invested - (avg_cost × shares_sold)
new_shares = old_shares - shares_sold
avg_cost stays the same (reducing position at existing avg cost)
```

### If All Shares Sold
```
position becomes null (reset — ready for a new position)
```

## Operations

All operations act on Robinhood Watchlists. **Every state-modifying step requires explicit
user confirmation before the MCP call.**

### Create a Basket
1. Ask the user for: basket name, description, and initial holdings
2. For each holding, ask for the stock symbol and target weight
3. Use Robinhood MCP `search` to resolve company names to ticker symbols if needed
4. Use `get_equity_quotes` to verify symbols are valid and show current prices
5. Validate weights (warn if they don't sum to 100%)
6. Generate the slug from the name (lowercase, hyphens for spaces, strip special chars).
   Slugs are truncated to 24 characters in the metadata — warn if two baskets would collide.
7. Build the `display_description` with `encode_watchlist_metadata(slug, target_weights,
   rebalance_threshold_pct)` and no snapshot (no positions yet). If encoding raises because
   the payload exceeds 256 characters, tell the user the basket has too many symbols.
8. Confirm with the user, then call `create_watchlist` with `display_name` = `Basket: <Name>`,
   the encoded `display_description`, and `icon_emoji` `🧺`
9. Call `add_to_watchlist` with the symbol list
10. Verify by re-fetching via `get_watchlists` and decoding, then display the basket summary

### View a Basket
1. Fetch via `get_watchlists` and decode with `watchlist_to_basket_dict`
2. Use `get_equity_quotes` to fetch current prices for all holdings
3. For holdings with positions, calculate:
   - **Current Value** = shares × current_price
   - **Total P&L ($)** = current_value - total_invested
   - **Total P&L (%)** = total_pnl / total_invested × 100
   - **Day Change ($)** = shares × (current_price - previous_close)
4. Present a formatted table:

| Symbol | Target Wt | Shares | Avg Cost | Current | Value | Day Chg | Total P&L |
|---|---|---|---|---|---|---|---|
| WDC | 20% | 12.66 | $79.00 | $82.50 | $1,044.45 | +$15.19 | +$44.45 (+4.44%) |
| MU | 20% | — | — | $105.20 | — | — | No position |

5. Show basket totals: Total Invested, Current Value, Day Change, Total P&L

### List All Baskets
1. Call `get_watchlists` and keep entries whose description decodes as basket metadata
   (`basket_summary.py --watchlists-json` does this filtering for you)
2. For each, display: Name, # of Holdings, Total Invested, Weight status
3. Present as a summary table

### Edit a Basket
1. Fetch the Watchlist via `get_watchlists` and decode the existing metadata
2. Support operations:
   - **Add holding**: add the symbol to `target_weights`, then `add_to_watchlist`
   - **Remove holding**: warn if it has an active position, then `remove_from_watchlist`
     and drop it from `target_weights` — note this also discards its snapshot entry
   - **Update weight**: change the symbol's entry in `target_weights`
   - **Rename**: `update_watchlist` with a new `display_name` (keep the `Basket:` prefix)
3. Re-encode with `encode_watchlist_metadata`, **preserving the existing snapshot**
4. Confirm with the user, call `update_watchlist`, then re-fetch and decode to verify

### Delete a Basket
1. Confirm with the user before deleting
2. Warn if any holdings have active positions
3. Delete the Watchlist on Robinhood
4. Note: deleting the basket does **not** sell anything — the underlying positions remain

### Record a Transaction
Cloud baskets store a running `shares`/`avg_cost` snapshot rather than a transaction log.
1. Fetch the Watchlist via `get_watchlists`
2. Compute the new description with
   `update_basket_watchlist_baseline(display_description, symbol, side, shares, price)`.
   This raises `ValueError` if the basket has outgrown the 256-char limit — if that
   happens the trade has already filled, so tell the user plainly that the fill
   succeeded but could not be recorded, and that the basket needs fewer symbols.
   Pass the **fill** price (`average_price`), never the order's `price` field, which
   is the limit/reference price and differs from what actually executed.
3. Confirm with the user, then call `update_watchlist`
4. **Verify**: re-fetch and decode to confirm the snapshot persisted; report the new position
5. **If the update fails or looks wrong**: rebuild with
   `reconstruct_basket_positions(orders, basket_symbols, snapshot)` using `get_equity_orders`,
   then retry `update_watchlist`

### Compare Baskets
1. Fetch both Watchlists and decode each with `watchlist_to_basket_dict`
2. Present side-by-side: shared symbols, unique symbols, weight differences
3. If positions exist, compare performance metrics
4. Use `get_equity_quotes` to show current prices for all symbols

## Utility Scripts

Reusable scripts packaged with this skill. Use these instead of writing ad-hoc Python code for basket operations. All scripts use Python stdlib only (no pip dependencies).

### Locating the scripts

The scripts live in the `scripts/` directory **next to this SKILL.md**. Resolve that path for
the current environment before running anything — do not assume the working directory is a
Tradethos checkout:

- **Claude Code (installed plugin)**: `${CLAUDE_PLUGIN_ROOT}/skills/basket-manager/scripts/`
- **Local checkout**: `skills/basket-manager/scripts/` from the repo root

The examples below use `$SCRIPTS` for that directory.

### Input modes

- `--watchlists-json '<json>'` — **primary**, supported by all four scripts. Pass the
  `get_watchlists` response through unchanged: it arrives double-nested as
  `{"data": {"watchlists": [...]}}`, and the scripts unwrap that themselves (a bare array
  or a `{"watchlists": …}` / `{"results": …}` envelope also work). Watchlists without
  decodable basket metadata are skipped, so passing the user's full list is fine — note
  Robinhood omits `display_description` entirely on lists that never had one.
- `--basket <slug>` — local `data/baskets/*.json` files (legacy/migration). Supported by
  `list_symbols.py`, `calc_performance.py`, and `calc_drift.py`. **Not** `basket_summary.py`.
- `--all` — all local basket files. Supported by `calc_performance.py` and `calc_drift.py`
  only. **Not** `list_symbols.py` or `basket_summary.py`.

When in doubt, run the script with `--help` rather than assuming a flag exists.

### `$SCRIPTS/list_symbols.py` — Extract symbols from baskets
```bash
python3 "$SCRIPTS/list_symbols.py" --watchlists-json '<get_watchlists output>' --format json
python3 "$SCRIPTS/list_symbols.py" --basket storage-and-memory-index   # Local file
```

### `$SCRIPTS/basket_summary.py` — Quick overview of all baskets
```bash
python3 "$SCRIPTS/basket_summary.py" --watchlists-json '<get_watchlists output>'
python3 "$SCRIPTS/basket_summary.py" --format json
```

### `$SCRIPTS/calc_performance.py` — Calculate P&L with live prices
Prices are passed as a `--prices` JSON argument. The agent fetches prices via Robinhood MCP, then passes them in.
```bash
python3 "$SCRIPTS/calc_performance.py" --watchlists-json '<get_watchlists output>' \
  --prices '{"WDC":560.00,"STX":920.00,"MU":985.19}'
python3 "$SCRIPTS/calc_performance.py" --basket <slug> --prices '{...}' --format table
```

### `$SCRIPTS/calc_drift.py` — Weight drift analysis for rebalancing
Threshold precedence: explicit `--threshold` → per-basket `rebalance_threshold_pct` → `config.json` → 5.0.

Note the `config.json` tier only applies to **local basket files that omit the field**.
Cloud baskets always carry a threshold once decoded (defaulting to 5.0), so
`rebalancing.default_threshold_pct` never takes effect for them — pass `--threshold`
explicitly if the user wants a different value applied to a watchlist-backed basket.
```bash
python3 "$SCRIPTS/calc_drift.py" --watchlists-json '<get_watchlists output>' \
  --prices '{"WDC":560.00,"STX":920.00,"MU":985.19}'
python3 "$SCRIPTS/calc_drift.py" --all --prices '{...}' --threshold 3.0   # Force a threshold
```

### Script Design Principles
- **No network calls** — scripts are pure data processors; the agent provides prices
- **JSON output by default** — structured for agent consumption; `--format table` for human display
- **Consistent CLI** — all scripts support `--format`, `--watchlists-json`, and `--help`
- **Zero dependencies** — Python stdlib only (`json`, `argparse`, `pathlib`)

## Cross-Skill Integration

- After creating a basket, suggest using the **trade-executor** skill to buy the holdings
- When viewing a basket, offer to run **stock-researcher** on any holding for deeper analysis
- Suggest the **portfolio-tracker** skill to compare actual Robinhood positions against basket targets
- When the **stock-screener** finds interesting stocks, offer to add them to a basket
- After a trade fills via **trade-executor**, offer to record the transaction in the relevant basket

## Example Interactions

**User**: "Create a basket called AI Leaders with NVDA at 30%, MSFT at 25%, GOOGL at 25%, and META at 20%"
→ Validate symbols with live quotes → encode metadata → confirm → `create_watchlist` `Basket: AI Leaders` + `add_to_watchlist` → verify by re-fetching

**User**: "Show me my baskets"
→ `get_watchlists` → keep entries with decodable basket metadata → summary table with invested amounts

**User**: "I just bought 10 shares of WDC at $79. Record that in my Storage basket."
→ Fetch Watchlist → `update_basket_watchlist_baseline` → confirm → `update_watchlist` → re-fetch to verify

**User**: "How's my Storage & Memory basket doing?"
→ Fetch Watchlist → `watchlist_to_basket_dict` → fetch quotes → calculate P&L per holding and totals

**User**: "Add AMZN at 15% to my AI Leaders basket"
→ Fetch Watchlist → add AMZN to target weights (preserving the snapshot) → warn about weight sum → confirm → `update_watchlist` + `add_to_watchlist`
