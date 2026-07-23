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
- **Account Selection**: Never auto-default `account_number` from `get_accounts` — always present available accounts and ask the user to choose or confirm.
- **Cross-Skill Offers**: Frame rebalancing trades or basket adjustments as optional suggestions/offers, never automatic actions.

## Available Data Sources (Robinhood MCP)

| Tool | Purpose |
|---|---|
| `get_portfolio` | Account-level value breakdown (equity, options, crypto) and buying power |
| `get_equity_positions` | Current holdings: symbol, quantity, average cost |
| `get_equity_quotes` | Real-time prices for P&L calculation (includes previous close for day change) |
| `get_pnl_trade_history` | Per-trade realized P&L (chronological, paginated) |
| `get_realized_pnl` | Aggregate realized P&L over time windows |
| `get_equity_tax_lots` | Tax-lot level position detail (cost basis per lot) |
| `get_equity_orders` | Recent order history |

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

1. Load the basket JSON from `data/baskets/{name}.json`
2. Call `get_equity_quotes(symbols=[all holding symbols])` for current prices and previous close
3. For each holding with a position (`position != null`), calculate:
   - **Current Value** = position.shares × current_price
   - **Day Change ($)** = position.shares × (current_price - previous_close)
   - **Day Change (%)** = (current_price - previous_close) / previous_close × 100
   - **Total P&L ($)** = current_value - position.total_invested
   - **Total P&L (%)** = total_pnl / position.total_invested × 100
4. Present per-holding table:

| Symbol | Target Wt | Shares | Avg Cost | Current | Value | Day Chg | Total P&L |
|---|---|---|---|---|---|---|---|
| WDC | 20% | 12.66 | $79.00 | $82.50 | $1,044.45 | +$15.19 (+1.87%) | +$44.45 (+4.44%) |
| MU | 20% | — | — | $105.20 | — | — | No position |

5. Show basket totals:
   - **Total Invested**: Sum of all position.total_invested
   - **Current Value**: Sum of all (shares × current_price)
   - **Day Change**: Sum of all day changes
   - **Total P&L**: Current Value - Total Invested ($ and %)

### 5. Basket Drift Analysis

When the user has a basket and asks "am I on track?" or "do I need to rebalance?":

1. Load the basket JSON from `data/baskets/`
2. Call `get_equity_quotes(symbols=[...])` for current prices
3. For each holding with a position, calculate actual weights:
   - total_basket_value = sum of (shares × current_price) for all positioned holdings
   - actual_weight[symbol] = (shares × current_price) / total_basket_value × 100
4. Compare actual vs target weights:

| Symbol | Target Weight | Actual Weight | Drift | Action Needed |
|---|---|---|---|---|
| NVDA | 30.0% | 35.2% | +5.2% | Overweight — consider trimming |
| MSFT | 25.0% | 22.1% | -2.9% | Slightly underweight |
| GOOGL | 25.0% | 24.8% | -0.2% | On target ✅ |
| META | 20.0% | 17.9% | -2.1% | Underweight — consider adding |

5. Flag any holding with drift > 5% as needing attention
6. If the user wants to rebalance, calculate the specific trades needed and suggest the **trade-executor** skill

### 6. Tax Lot Detail

When the user asks about cost basis or tax lots:

1. Call `get_equity_tax_lots(account_number, symbol)` for a specific stock
2. Present each lot: Lot ID, Quantity, Purchase Price, Purchase Date, Current P&L
3. Useful for tax-loss harvesting decisions or specific-lot selling

## Drift Thresholds

| Drift Level | Range | Flag |
|---|---|---|
| On Target | ±2% | ✅ No action needed |
| Minor Drift | ±2-5% | ⚠️ Monitor |
| Significant Drift | > ±5% | 🔴 Consider rebalancing |

## Tracking Unowned Basket Stocks

If a basket includes stocks the user doesn't have a position in (`position: null`):

1. Show them in the performance table with "No position" marker
2. Calculate the dollar amount needed to reach target weight based on basket's total_invested
3. Suggest buying via the trade-executor skill

## Cross-Skill Integration

- When drift is significant, suggest rebalancing trades via the **trade-executor** skill
- When a position is performing unusually, suggest deeper research via the **stock-researcher** skill
- Drift analysis and basket performance reference basket files from the **basket-manager** skill
- After viewing positions, offer to create a basket from current holdings (basket-manager)
- After a trade fills, suggest recording the transaction in the relevant basket (basket-manager)

## Example Interactions

**User**: "How are my stocks doing?"
→ Fetch positions + quotes → Show table with unrealized P&L → Show total

**User**: "How's my Storage & Memory basket doing?"
→ Load basket → Fetch quotes → Calculate day change + total P&L per holding → Present table with totals

**User**: "Am I aligned with my AI Leaders basket?"
→ Load AI Leaders basket → Fetch quotes → Show drift table → Flag outliers

**User**: "How much have I made this month?"
→ Call `get_realized_pnl` with `span=month` → Present aggregate + breakdown

**User**: "Show me the tax lots for my AAPL position"
→ Call `get_equity_tax_lots` for AAPL → Present lot-level detail

**User**: "What's my buying power?"
→ Call `get_portfolio` → Present buying power and account summary
