---
name: trade-executor
description: >
  Place, review, and cancel equity orders through the Robinhood brokerage with built-in safety rails.
  Supports market, limit, stop, and stop-limit orders with proper handling of market hours,
  fractional shares, and dollar-amount orders. Always reviews orders before placement unless
  the user explicitly requests to skip. Use this skill when the user wants to buy or sell stocks,
  execute basket-aligned trades, or manage open orders.
---

# Trade Executor

Place, review, and manage equity orders through Robinhood with safety-first workflows. Every order goes through a review step before execution unless the user explicitly bypasses it.

## When to Use This Skill

Trigger this skill when the user mentions:
- Buying or selling a stock
- Placing an order (market, limit, stop)
- Executing a trade
- Canceling an order
- Checking order status
- "Invest in" or "pick up shares of"
- Executing basket-aligned trades
- Dollar-cost averaging

## General Rules & Safety Standards

- **State-Modifying Confirmation**: All operations that modify state (place order, cancel order) require **explicit user confirmation** before execution.
- **Formatting**: Present monetary values with proper formatting (e.g., `$1,234.56`). Present percentages to two decimal places (e.g., `12.34%`).
- **Stock Context**: Always include symbol, current price, and day change context when presenting stock quotes.
- **Account Selection**: Never auto-default `account_number` from `get_accounts` — always present available accounts and ask the user to choose or confirm.
- **Research-First**: If the user hasn't researched a stock prior to wanting to buy, suggest running stock research first before placing orders.
- **Config Resolution Hierarchy**: Order review policies & defaults resolve in 3 tiers: User explicit prompt request → Root `config.json` (`trading.require_review_by_default`) → Built-in skill fallback (`require_review = true`).

## Safety Rails — CRITICAL

### Review-Before-Place (Default)
1. **Always** call `review_equity_order` first
2. Present the review results to the user:
   - Estimated cost / proceeds
   - Current quote (bid/ask/last)
   - Any pre-trade alerts (buying power, PDT, halts)
3. Get **explicit user confirmation** ("yes", "go ahead", "place it")
4. Only then call `place_equity_order`

### Review Bypass (Exception Only)
Skip the review step ONLY when the user has **very explicitly** asked to bypass:
- ✅ "Skip the review and just place it"
- ✅ "Don't review, just buy"
- ❌ "Buy 10 shares of AAPL" — this is NOT a bypass
- ❌ "Place this order" — this is NOT a bypass

### Account Number Rules
- **Never** auto-default `account_number` from `get_accounts`
- If the user hasn't specified their account, call `get_accounts` and ask them to choose
- Once established in a conversation, you can reuse the same account for subsequent orders
- The account must have `agentic_allowed=true` — non-agentic accounts are rejected

## Order Placement Workflow

```
1. Identify: symbol, side (buy/sell), order type, quantity or dollar amount
       ↓
2. Validate: get_equity_tradability → confirm the symbol is tradable
       ↓
3. Quote: get_equity_quotes → show current price to the user
       ↓
4. Review: review_equity_order → present estimated cost + alerts
       ↓
5. Confirm: Get explicit user confirmation
       ↓
6. Execute: place_equity_order → with fresh UUID ref_id
       ↓
7. Verify: get_equity_orders → confirm order status
```

## Order Types

### Market Order
- Executes immediately at the best available price
- **Regular hours only** (`market_hours=regular_hours`, 9:30–16:00 ET)
- Supports fractional shares and dollar-amount orders
- For immediate fills with price protection, **prefer a marketable limit at the current ask** over a plain market order

### Limit Order
- Executes only at the specified price or better
- Works in all sessions: regular, extended, all_day
- Required parameters: `limit_price`
- **Use for extended/overnight hours** — market orders don't work outside regular hours

### Stop Market Order
- Triggers a market order when the stock hits the stop price
- **Regular hours only**
- Required parameters: `stop_price`

### Stop Limit Order
- Triggers a limit order when the stock hits the stop price
- **Regular hours only**
- Required parameters: `stop_price`, `limit_price`

## Market Hours Logic

| Session | Value | Hours (ET) | Allowed Order Types |
|---|---|---|---|
| Regular | `regular_hours` | 9:30–16:00 | All types |
| Extended | `extended_hours` | Pre/post-market | Limit only |
| Overnight/24hr | `all_day_hours` | 24-hour session | Limit only |

**Key rule**: If the user wants an immediate fill outside regular hours, place a **limit order at the current ask** with the appropriate `market_hours` value — NOT a market order.

## Quantity vs Dollar Amount

- Provide **exactly one** of `quantity` or `dollar_amount`, never both
- `dollar_amount` requires `type=market` (the server computes shares from last_trade_price)
- Fractional shares: only with `type=market` + `market_hours=regular_hours`, up to 6 decimal places, no short sells

## Idempotency

- Generate a **fresh UUID** as `ref_id` for each new logical order
- If a `place_equity_order` call fails due to a transient transport error, **retry with the same ref_id**
- Use a **new ref_id** only when the user wants a genuinely new/different order
- Omitting `ref_id` falls back to a server-generated key (loses client-side idempotency)

## Time in Force

- `gfd` (Good For Day) — expires at end of trading day (default)
- `gtc` (Good Till Cancelled) — remains active until filled or cancelled
- If the user doesn't specify, default to `gfd`
- For limit orders where the user wants to "wait for the price to drop," suggest `gtc`

## Order Management

### Check Order Status
- Use `get_equity_orders` with the `account_number`
- Filter by `symbol`, `state`, or `order_id` for specific lookups
- States: new, queued, confirmed, partially_filled, filled, cancelled, rejected, failed

### Cancel an Order
1. Use `get_equity_orders` to find the order (by symbol or description)
2. Present the order details to the user
3. Get **explicit confirmation** to cancel
4. Call `cancel_equity_order` with the `order_id`
5. Note: cancellation may be rejected if the order has already filled

### View Order History
- Use `get_equity_orders` with date filters (`created_at_gte`)
- Filter by `placed_agent=agentic` to see only MCP-placed orders
- Present as a table: Symbol, Side, Type, Quantity, Price, Status, Date

## Basket-Aligned Trading

When the user has custom baskets (from the basket-manager skill):

### Execute to Target Weights
1. Load the basket JSON from `data/baskets/`
2. Use `get_portfolio` to get current buying power
3. Use `get_equity_positions` to get current holdings
4. Use `get_equity_quotes` for current prices
5. Calculate the trades needed to align actual holdings with target weights
6. Present the trade plan as a table: Symbol, Current Weight, Target Weight, Action (Buy/Sell), Shares, Est. Cost
7. Get confirmation, then execute each trade in sequence

### Dollar-Based Basket Buy
When the user says "invest $X in my basket":
1. Load the basket JSON
2. Divide $X according to target weights
3. For each holding: calculate dollar allocation → use dollar_amount market orders
4. Present the plan, get confirmation, execute

### Recording Basket Transactions
When a trade is executed **at the basket level** (e.g. "invest $500 in my AI Leaders basket" or "buy 10 shares of WDC for my Storage basket"):
1. Load the target basket JSON file from `data/baskets/{slug}.json`.
2. Record the transaction in that specific basket's transaction history.
3. Recalculate that basket's `shares`, `avg_cost`, and `total_invested`.
4. Trades placed outside of a basket execution (regular standalone stock buys/sells) are **not** recorded into basket files, keeping custom basket histories completely separate and free of conflict.

## Tax-Lot Selling (Advanced)

For sell orders where the user wants to select specific tax lots:
1. Call `get_equity_tax_lots` for the symbol
2. Present available lots with: lot ID, quantity, cost basis, date acquired
3. Let the user choose which lots to sell
4. Pass `tax_lots` as `{open_lot_id, quantity}` pairs to the order
- Only for sell orders, US accounts, max 30 lots
- Not allowed with dollar_amount, stop orders, all_day_hours, or fractional limit orders

## Cross-Skill Integration

- Before trading an unfamiliar stock, suggest the **stock-researcher** skill for analysis
- After executing trades, suggest the **portfolio-tracker** skill to monitor positions
- When buying stocks that aren't in a basket, offer to add them via the **basket-manager** skill
- After a fill, offer to record the transaction in the relevant basket (basket-manager skill)

## Example Interactions

**User**: "Buy 10 shares of AAPL"
→ Check tradability → Get quote → Review order (market, 10 shares) → Show cost estimate → Wait for confirmation → Place order → Show confirmation

**User**: "Set a limit order for TSLA at $200"
→ Ask: buy or sell? How many shares? → Review → Confirm → Place

**User**: "Invest $5000 in my AI Leaders basket"
→ Load basket → Calculate allocations → Present plan → Confirm → Execute orders → Offer to record transactions

**User**: "Cancel my pending NVDA order"
→ Get orders filtered by NVDA → Show pending orders → Confirm which to cancel → Cancel
