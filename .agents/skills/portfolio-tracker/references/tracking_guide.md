# Portfolio Tracking Workflows Reference

Detailed workflows for position monitoring, P&L tracking, basket performance, and drift analysis.

## Workflow: Full Position Review

```
Step 1: Get account overview
  └─ get_portfolio(account_number) → total value, buying power

Step 2: Get positions (parallel with quotes)
  ├─ get_equity_positions(account_number) → all open positions
  └─ (after positions) get_equity_quotes(symbols=[all position symbols]) → current prices

Step 3: Calculate metrics per position
  ├─ Market Value = quantity × current_price
  ├─ Cost Basis = quantity × average_cost
  ├─ Unrealized P&L = market_value - cost_basis
  └─ P&L % = unrealized_pnl / cost_basis × 100

Step 4: Present results
  ├─ Positions table (sorted by market value, descending)
  ├─ Totals row
  └─ Buying power note
```

## Workflow: Basket Performance

```
Step 1: Load basket
  └─ Read data/baskets/{name}.json

Step 2: Get current quotes
  └─ get_equity_quotes(symbols=[all holding symbols])
      → Returns: current price, previous_close, day change

Step 3: Calculate per-holding metrics (for holdings with position != null)
  ├─ current_value = position.shares × current_price
  ├─ day_change_$ = position.shares × (current_price - previous_close)
  ├─ day_change_% = (current_price - previous_close) / previous_close × 100
  ├─ total_pnl_$ = current_value - position.total_invested
  └─ total_pnl_% = total_pnl_$ / position.total_invested × 100

Step 4: Calculate basket totals
  ├─ basket_total_invested = sum of all position.total_invested
  ├─ basket_current_value = sum of all (shares × current_price)
  ├─ basket_day_change = sum of all day_change_$
  ├─ basket_total_pnl_$ = basket_current_value - basket_total_invested
  └─ basket_total_pnl_% = basket_total_pnl_$ / basket_total_invested × 100

Step 5: Present results
  ├─ Per-holding table with all metrics
  ├─ Basket summary row
  └─ Holdings with no position shown as "No position"
```

## Workflow: Basket Drift Analysis

```
Step 1: Load basket
  └─ Read data/baskets/{name}.json

Step 2: Get current quotes
  └─ get_equity_quotes(symbols=[all holding symbols])

Step 3: Calculate actual weights
  ├─ For each positioned holding: value = position.shares × current_price
  ├─ total_basket_value = sum of all positioned holding values
  └─ actual_weight[symbol] = value / total_basket_value × 100

Step 4: Compare target vs actual
  For each holding:
  ├─ drift = actual_weight - target_weight
  ├─ flag = "on target" if |drift| ≤ 2%, "minor" if 2-5%, "significant" if > 5%
  └─ If position is null → actual_weight = 0%, drift = -target_weight

Step 5: Calculate rebalancing trades (if requested)
  For each holding with drift:
  ├─ target_value = total_basket_value × target_weight / 100
  ├─ current_value = position.shares × current_price (or 0 if no position)
  ├─ trade_value = target_value - current_value
  ├─ If trade_value > 0 → Buy ceil(trade_value / current_price) shares
  └─ If trade_value < 0 → Sell floor(|trade_value| / current_price) shares
```

## Workflow: P&L Review

```
Step 1: Determine time window
  ├─ "today" → span=day
  ├─ "this week" → span=week  
  ├─ "this month" → span=month
  ├─ "last 90 days" / "this quarter" → span=3month
  ├─ "this year" → span=year
  └─ "all time" → span=all

Step 2: Get aggregate P&L
  └─ get_realized_pnl(account_number, span=X, asset_classes=["equity"])

Step 3: Get trade details (if user wants specifics)
  └─ get_pnl_trade_history(account_number, span=X)
  └─ Paginate with cursor if needed

Step 4: Present results
  ├─ Aggregate: total realized gain/loss, number of trades
  ├─ Breakdown by time bucket (daily/weekly depending on span)
  └─ Optional: per-trade detail table
```

## Average Cost Recalculation

When reviewing basket positions, the avg_cost in the JSON is the source of truth. It's updated on each transaction:

### Buy
```
new_total_invested = old_total_invested + buy_amount
new_shares = old_shares + buy_shares
new_avg_cost = new_total_invested / new_shares
```

### Sell
```
realized_pnl = (sell_price - avg_cost) × shares_sold
new_total_invested = old_total_invested - (avg_cost × shares_sold)
new_shares = old_shares - shares_sold
avg_cost stays the same
```

### Full Exit
```
If new_shares == 0 → position becomes null (clean slate)
```

## Calculating Basket Concentration

When reviewing basket positions, calculate concentration risk:

```
Concentration metrics:
  ├─ Largest position weight (by actual value, not target)
  ├─ Top 3 positions as % of total basket value
  ├─ Number of positioned holdings vs total holdings
  └─ HHI (Herfindahl-Hirschman Index) = sum of (actual_weight²) for all positioned holdings
      - HHI < 1500: Well diversified
      - HHI 1500-2500: Moderately concentrated
      - HHI > 2500: Highly concentrated
```

## Tax-Loss Harvesting Check

When the user asks about tax optimization:

1. Get all positions with unrealized losses (from basket data or Robinhood positions)
2. For each loss position, check:
   - Is the loss > $100? (worth harvesting)
   - Was the stock purchased > 30 days ago? (wash sale rule — check transaction dates)
   - Would selling trigger a wash sale with recent buys?
3. Present harvesting candidates with estimated tax savings
4. Note: This is informational only — always recommend consulting a tax advisor
