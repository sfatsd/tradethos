---
name: basket-manager
description: >
  Create and manage custom stock baskets (user-defined indices with target allocations and
  position tracking). Supports creating, viewing, editing, and deleting basket definitions
  stored as local JSON files. Tracks actual positions with transaction history, average cost,
  and performance metrics. Use this skill when the user wants to build a custom basket of stocks,
  set target weights, track a model basket, or manage their investment thesis for a group of holdings.
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

## Basket Storage

Baskets are stored as JSON files in the `baskets/` directory within this skill:
- **Path**: `data/baskets/{slug}.json`
- **Slug**: Derived from the basket name using lowercase kebab-case (e.g., "Storage & Memory Index" → `storage-and-memory-index.json`)
- **Gitignored**: Basket data files are not committed (they contain personal investment data)
- **No external database needed** — everything is file-based

## Basket JSON Schema

Each basket file follows this structure:

```json
{
  "name": "Storage & Memory Index",
  "description": "Custom basket of leading enterprise data storage leaders",
  "created_at": "2026-07-22T15:00:00Z",
  "updated_at": "2026-07-22T22:30:00Z",
  "total_invested": 5000.00,
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

### Create a Basket
1. Ask the user for: basket name, description, and initial holdings
2. For each holding, ask for the stock symbol and target weight
3. Use Robinhood MCP `search` to resolve company names to ticker symbols if needed
4. Use `get_equity_quotes` to verify symbols are valid and show current prices
5. Validate weights (warn if they don't sum to 100%)
6. Generate the slug from the name (lowercase, hyphens for spaces, strip special chars)
7. Set all `position` fields to `null` (no positions yet) and `total_invested` to 0
8. Write the JSON file to `baskets/{slug}.json`
9. Confirm creation and display the basket summary

### View a Basket
1. Read the JSON file from `baskets/`
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
1. List all `.json` files in the `baskets/` directory (excluding `.gitkeep`)
2. For each, read the file and display: Name, # of Holdings, Total Invested, Description
3. Present as a summary table

### Edit a Basket
1. Load the existing basket JSON
2. Support operations:
   - **Add holding**: Add a new stock with target weight and `position: null`
   - **Remove holding**: Remove a stock (warn if it has an active position)
   - **Update weight**: Change a holding's target weight
   - **Update thesis**: Change a holding's investment thesis
   - **Rename**: Change the basket name (rename the file too)
3. Update the `updated_at` timestamp
4. Write the modified JSON back to the file

### Delete a Basket
1. Confirm with the user before deleting
2. Warn if any holdings have active positions
3. Remove the JSON file from `baskets/`

### Record a Transaction
1. Load the basket JSON
2. Find the holding by symbol
3. If `position` is `null`, initialize it: `{ "shares": 0, "avg_cost": 0, "total_invested": 0, "transactions": [] }`
4. Add the transaction to the `transactions` array
5. Recalculate `shares`, `avg_cost`, and `total_invested` using the formulas above
6. Update basket-level `total_invested` (sum of all holdings)
7. Update `updated_at` timestamp
8. Write back to file

### Compare Baskets
1. Load two basket JSON files
2. Present side-by-side: shared symbols, unique symbols, weight differences
3. If positions exist, compare performance metrics
4. Use `get_equity_quotes` to show current prices for all symbols

## Cross-Skill Integration

- After creating a basket, suggest using the **trade-executor** skill to buy the holdings
- When viewing a basket, offer to run **stock-researcher** on any holding for deeper analysis
- Suggest the **portfolio-tracker** skill to compare actual Robinhood positions against basket targets
- When the **stock-screener** finds interesting stocks, offer to add them to a basket
- After a trade fills via **trade-executor**, offer to record the transaction in the relevant basket

## Example Interactions

**User**: "Create a basket called AI Leaders with NVDA at 30%, MSFT at 25%, GOOGL at 25%, and META at 20%"
→ Validate symbols, create `ai-leaders.json` with all positions as null, confirm with live prices

**User**: "Show me my baskets"
→ List all JSON files in baskets/, display summary table with invested amounts

**User**: "I just bought 10 shares of WDC at $79. Record that in my Storage basket."
→ Load basket, add buy transaction, update avg_cost/shares/total_invested, save

**User**: "How's my Storage & Memory basket doing?"
→ Load basket, fetch quotes, calculate P&L per holding and totals, present table

**User**: "Add AMZN at 15% to my AI Leaders basket"
→ Load basket, add AMZN with position: null, warn about weight sum, save
