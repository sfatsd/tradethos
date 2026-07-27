# Robinhood Trading Skills — Project Rules

## General Rules
- All brokerage operations that modify state (place/cancel orders, create watchlists) require **explicit user confirmation** before execution.
- Never auto-default `account_number` from `get_accounts` — always present available accounts and ask the user to choose or confirm.
- Present monetary values with proper formatting (e.g., `$1,234.56`). Present percentages to two decimal places (e.g., `12.34%`).
- When presenting stock data, always include the symbol, current price, and relevant context (e.g., day change).

## Custom Baskets
- Custom baskets (user-defined indices) are stored cloud-natively as Robinhood Watchlists named `Basket: <Name>` (e.g., `Basket: Storage Leaders`).
- Basket metadata (slug, target model weights, rebalance threshold, baseline holding snapshot) is encoded in the Watchlist `display_description` using `zlib` Base64 compression (`Z64:...`) to fit up to 30+ symbols under Robinhood's 256-character limit.
- Holdings and cost basis are calculated from baseline snapshots + filled orders retrieved via `get_equity_orders`.
- After trade fills, baseline snapshots are updated in-place via `update_watchlist` so metadata size never grows over time.

## Research-First Approach
- When a user expresses interest in buying a stock they haven't researched yet, suggest running research first before proceeding to trade.
- After research, offer to add the stock to a custom basket if the user expresses positive sentiment.

## Safety Rails
- For trade execution, always call `review_equity_order` before `place_equity_order` unless the user has **very explicitly** asked to skip the review (e.g., "skip the review", "just place it").
- A generic "place this order" or "buy AAPL" is **NOT** a review bypass.
- Always use `get_equity_tradability` to verify a symbol is tradable before attempting an order.
- Generate a fresh UUID `ref_id` for each new logical order. Reuse the same `ref_id` only when retrying a failed transport.

## Skill Coordination
- Skills can reference each other. For example, the trade-executor skill can read basket files to suggest basket-aligned trades.
- When suggesting cross-skill actions (e.g., "would you like to add this to a basket?"), frame them as offers, not automatic actions.
- After a trade fills for a stock in a basket, offer to record the transaction in the basket file.
