# Order Rules Reference

Consolidated parameter rules for Robinhood equity orders, extracted from the MCP tool schemas.

## Required Parameters (All Orders)

| Parameter | Description | Rules |
|---|---|---|
| `account_number` | Brokerage account number | Must come from user, never auto-default. Must be `agentic_allowed=true`. |
| `symbol` | Stock ticker symbol | Uppercase, exact match |
| `side` | `buy` or `sell` | Required |
| `type` | Order type | `market`, `limit`, `stop_market`, or `stop_limit` |

## Conditional Parameters

| Parameter | Required When | Notes |
|---|---|---|
| `limit_price` | `type=limit` or `type=stop_limit` | Price string (e.g., `"150.00"`) |
| `stop_price` | `type=stop_market` or `type=stop_limit` | Trigger price string |
| `quantity` | Always (unless using `dollar_amount`) | Number of shares as string. Decimals allowed for market + regular_hours. |
| `dollar_amount` | Alternative to `quantity` | USD notional (e.g., `"100.00"`). Only valid with `type=market`. |

## Mutual Exclusions

- Provide **exactly one** of `quantity` OR `dollar_amount`, never both
- `dollar_amount` requires `type=market`

## Optional Parameters

| Parameter | Default | Options |
|---|---|---|
| `time_in_force` | `gfd` | `gfd` (good for day), `gtc` (good till cancelled) |
| `market_hours` | `regular_hours` | `regular_hours`, `extended_hours`, `all_day_hours` |
| `ref_id` | Server-generated | Client UUID for idempotency |
| `tax_lots` | FIFO (default) | Array of `{open_lot_id, quantity}` for sell orders |

## Market Hours Restrictions

| Order Type | regular_hours | extended_hours | all_day_hours |
|---|---|---|---|
| `market` | ✅ | ❌ | ❌ |
| `limit` | ✅ | ✅ | ✅ |
| `stop_market` | ✅ | ❌ | ❌ |
| `stop_limit` | ✅ | ❌ | ❌ |

**Outside regular hours**: Use limit orders only. For an immediate fill, place a marketable limit at the current ask.

## Fractional Shares Rules

- Only allowed with `type=market` + `market_hours=regular_hours`
- Eligible accounts only
- Up to 6 decimal places
- No short sells (buy only)
- Not allowed with `extended_hours` or `all_day_hours`

## Dollar-Amount Orders Rules

- Requires `type=market`
- Server computes shares from `last_trade_price`
- Only in `regular_hours`
- Not compatible with fractional limit orders

## Tax-Lot Selling Rules

- Sell orders only
- US accounts only
- Max 30 lots per order
- Lot quantities must sum to the order `quantity`
- Get lot IDs from `get_equity_tax_lots` first
- NOT allowed with:
  - `dollar_amount`
  - `stop_market` or `stop_limit`
  - `all_day_hours`
  - Fractional-share limit orders

## Idempotency Rules

1. Generate a fresh UUID for `ref_id` on each new logical order
2. On transient transport failure retry: reuse the SAME `ref_id`
3. For a genuinely new order: generate a NEW `ref_id`
4. Omitting `ref_id` means the server generates one (no client-side dedup)

## Order States

| State | Description |
|---|---|
| `new` | Just created |
| `queued` | Queued for submission |
| `confirmed` | Submitted to exchange |
| `unconfirmed` | Submission pending |
| `partially_filled` | Some shares filled |
| `filled` | Fully executed |
| `cancelled` | Cancelled by user or system |
| `rejected` | Rejected (insufficient funds, halted, etc.) |
| `failed` | System failure |
| `voided` | Voided |

## Best Practices

1. **Prefer marketable limit over plain market** for immediate fills with price protection
2. **Always check tradability** before placing orders
3. **Present the review** clearly with estimated cost and any alerts
4. **Log the ref_id** for each order for potential retry or audit
5. **Check order status** after placement to confirm execution
