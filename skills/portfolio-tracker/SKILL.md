---
name: portfolio-tracker
description: >
  Monitor equity positions, track profit & loss, and compare actual holdings against custom basket
  target allocations. Provides basket drift analysis and rebalancing suggestions.
  Use this skill when the user wants to check how their investments are performing,
  see their current positions, review P&L, or check if their portfolio needs rebalancing.
---

# Portfolio Tracker

Monitor live brokerage positions, track profit & loss, and analyze basket performance with daily and total change metrics.

## When to Use This Skill

Trigger this skill when the user mentions:
- Checking their positions or holdings
- Portfolio performance or value
- Profit and loss (P&L)
- "How are my stocks doing?"
- "What's my portfolio worth?"
- "How much have I made/lost?"
- Rebalancing or drift analysis
- Buying power or account value
- Tax lot details
- "How's my basket doing?"
- Basket performance or daily change

## General Rules & Standards

- **Formatting**: Present monetary values with proper formatting (e.g., `$1,234.56`). Present percentages to two decimal places (e.g., `12.34%`).
- **Stock Context**: Always include symbol, current price, and day change context when presenting position breakdown or basket performance.
- **Account Selection**: Default `account_number` to the account with `agentic_allowed: true` from `get_accounts`. Ask the user to choose only when more than one account has `agentic_allowed: true`, or when none do. If the user names a specific account, use that instead.
- **Cross-Skill Offers**: Frame rebalancing trades or basket adjustments as optional suggestions/offers, never automatic actions.

## Available Data Sources

| Tool / Helper | Purpose |
|---|---|
| `get_portfolio` | Account-level value breakdown (equity, options, crypto) and buying power |
| `get_equity_positions` | Current total account holdings: symbol, quantity, average cost |
| `get_equity_quotes` | Real-time prices for P&L calculation (includes previous close for day change) |
| `get_pnl_trade_history` | Per-trade realized P&L (chronological, paginated) |
| `get_realized_pnl` | Aggregate realized P&L over time windows |
| `get_equity_tax_lots` | Tax-lot level position detail (cost basis per lot) |
| basket-manager's `basket.py show <slug>` | A basket's target weights, thesis, and per-holding position (shares, average cost, realized profit and loss) |
| basket-manager's `basket.py verify --positions` | Compares a basket's claimed shares to the real account position; see "Basket Performance" below |
| basket-manager's `calc_drift.py` | Weight drift per holding against the basket's threshold |

A basket lives in the local event-log store at `~/.tradethos`, not on Robinhood. Reading a
basket needs no network call; only its live price and its position-check against the account
do.

## Core Workflows

### 1. Account Overview

When the user asks "what's my portfolio worth?" or "show me my account":

1. Call `get_portfolio(account_number)` for the value breakdown
2. Present:
   - Total portfolio value
   - Breakdown by asset type (equities, options, crypto)
   - Buying power (cash available to invest)
   - Today's change (if available)

### 2. Current Positions

When the user asks "what do I own?" or "show me my positions":

1. Call `get_equity_positions(account_number)` for all open positions
2. Call `get_equity_quotes(symbols=[...])` for current prices of all held symbols
3. For each position, calculate:
   - **Current Value** = quantity × current price
   - **Cost Basis** = quantity × average cost
   - **Unrealized P&L ($)** = current value - cost basis
   - **Unrealized P&L (%)** = (current value - cost basis) / cost basis × 100
4. Present as a table:

| Symbol | Shares | Avg Cost | Current Price | Market Value | Unrealized P&L | % Change |
|---|---|---|---|---|---|---|
| AAPL | 50 | $150.00 | $175.00 | $8,750.00 | +$1,250.00 | +16.67% |

5. Show totals at the bottom: Total Value, Total Cost, Total Unrealized P&L

### 3. Realized P&L

When the user asks "how much have I made?" or "show my P&L":

#### Per-Trade History
- Use `get_pnl_trade_history(account_number, span="month")` for recent trades
- Present each closed trade: Symbol, Side, Quantity, Price, Realized Gain/Loss
- Default span: `month`. Adjust based on user request (week, 3month, ytd, all)

#### Aggregate P&L
- Use `get_realized_pnl(account_number, span="3month")` for summary
- Present: Total realized gain/loss ($), number of trades, by time bucket
- Filter by `asset_classes=["equity"]` for stock-only P&L

### 4. Basket Performance

When the user asks "how's my basket doing?" or "show me my Storage basket":

1. Call the basket-manager skill's `basket.py show <slug> --prices '<json>'`, passing current
   prices fetched from `get_equity_quotes`. The tool returns each holding's target weight,
   position, current value, and profit or loss, and the basket's total current value.
2. Call `get_equity_quotes(symbols=[all holding symbols])` for previous close, to compute day
   change (the `show` output does not include it).
3. **Run `basket.py verify <slug> --positions '<the get_equity_positions response>'`.** This
   compares the basket's claimed shares to the real account position for each symbol and
   reports one of three states: `match`, `outside_shares` (the user holds more outside the
   basket — normal, no warning needed), or `over_claimed` (the user sold basket shares outside
   the basket — the record is wrong).
4. **If a symbol comes back `over_claimed`**: warn the user, and offer the repair from the
   basket-manager skill's "verify" section — find the sale order in `get_equity_orders`,
   confirm it with the user, then record it into the basket with `record-fills`.
5. For each holding with a position (`position != null`), calculate:
   - **Current Value** = position.shares × current_price
   - **Day Change ($)** = position.shares × (current_price - previous_close)
   - **Day Change (%)** = (current_price - previous_close) / previous_close × 100
   - **Total P&L ($)** = current_value - position.total_invested
   - **Total P&L (%)** = total_pnl / position.total_invested × 100
6. Present per-holding table:

| Symbol | Target Wt | Shares | Avg Cost | Current | Value | Day Chg | Total P&L |
|---|---|---|---|---|---|---|---|
| WDC | 20% | 12.66 | $79.00 | $82.50 | $1,044.45 | +$15.19 (+1.87%) | +$44.45 (+4.44%) |
| MU | 20% | — | — | $105.20 | — | — | No position |

7. Show basket totals:
   - **Total Invested**: Sum of all position.total_invested
   - **Current Value**: Sum of all (shares × current_price)
   - **Day Change**: Sum of all day changes
   - **Total P&L**: Current Value - Total Invested ($ and %)

### 5. Basket Drift Analysis

When the user has a basket and asks "am I on track?" or "do I need to rebalance?":

1. Call `get_equity_quotes` for the basket's holdings.
2. Call the basket-manager skill's `calc_drift.py --slug <slug> --prices '<json>'`. It
   compares each holding's actual weight (by current market value) to its target weight, and
   classifies the drift as `on_target`, `minor_drift`, or `significant_drift`.
3. Present the result as a table:

| Symbol | Target Weight | Actual Weight | Drift | Status |
|---|---|---|---|---|
| NVDA | 30.0% | 35.2% | +5.2% | significant_drift |
| MSFT | 25.0% | 22.1% | -2.9% | minor_drift |
| GOOGL | 25.0% | 24.8% | -0.2% | on_target |
| META | 20.0% | 17.9% | -2.1% | minor_drift |

4. Flag every `significant_drift` holding as needing rebalancing attention.
5. If the user wants to rebalance, calculate the specific trades needed and suggest the **trade-executor** skill.

### 6. Tax Lot Detail

When the user asks about cost basis or tax lots:

1. Call `get_equity_tax_lots(account_number, symbol)` for a specific stock
2. Present each lot: Lot ID, Quantity, Purchase Price, Purchase Date, Current P&L
3. Useful for tax-loss harvesting decisions or specific-lot selling

## Drift & Rebalancing Thresholds

`calc_drift.py` resolves the rebalance threshold with a 3-tier hierarchy:

1. **An explicit `--threshold` flag** to `calc_drift.py` (highest priority).
2. **The basket's own `rebalance_threshold_pct`**, set at `create` and changed with `basket.py
   set-threshold`.
3. **`rebalancing.default_threshold_pct` in `config.json`** at the repo root, else `5.0%`.
   `rebalancing.on_target_threshold_pct` in the same file sets the on-target threshold, else
   `2.0%`.

| Drift Level | Range | Flag |
|---|---|---|
| On Target | `\|drift\| <= 2.0%` (or `rebalancing.on_target_threshold_pct` in `config.json`) | ✅ No action needed |
| Minor Drift | `on_target_threshold_pct < \|drift\| < rebalance_threshold_pct` | ⚠️ Monitor |
| Rebalance Alert | `\|drift\| >= rebalance_threshold_pct` (fallback: `5.0%`) | 🔴 Consider rebalancing |

## Tracking Unowned Basket Stocks

If a basket includes stocks the user doesn't have a position in (`position: null`):

1. Show them in the performance table with "No position" marker
2. Calculate the dollar amount needed to reach target weight based on basket's total_invested
3. Suggest buying via the trade-executor skill

## Cross-Skill Integration

- When drift is significant, suggest rebalancing trades via the **trade-executor** skill
- When a position is performing unusually, suggest deeper research via the **stock-researcher** skill
- Drift analysis and basket performance read the local event-log store through the **basket-manager** skill's `basket.py` and `calc_drift.py`
- After viewing positions, offer to create a basket from current holdings (basket-manager)
- After a trade fills, offer to record it into the relevant basket with `record-fills` (basket-manager)

## Example Interactions

**User**: "How are my stocks doing?"
→ Fetch positions + quotes → Show table with unrealized P&L → Show total

**User**: "How's my Storage & Memory basket doing?"
→ `basket.py show storage-and-memory-index --prices '{...}'` → fetch quotes for day change →
present table with totals → run `verify --positions` and flag any `over_claimed` symbol

**User**: "Am I aligned with my AI Leaders basket?"
→ Load AI Leaders basket → Fetch quotes → Show drift table → Flag outliers

**User**: "How much have I made this month?"
→ Call `get_realized_pnl` with `span=month` → Present aggregate + breakdown

**User**: "Show me the tax lots for my AAPL position"
→ Call `get_equity_tax_lots` for AAPL → Present lot-level detail

**User**: "What's my buying power?"
→ Call `get_portfolio` → Present buying power and account summary
