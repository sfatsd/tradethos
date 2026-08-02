# Robinhood Trading Skills — Project Rules

## General Rules
- All brokerage operations that modify state (place/cancel orders, create watchlists) require **explicit user confirmation** before execution.
- Never auto-default `account_number` from `get_accounts` — present available accounts and ask the user to choose or confirm, unless the user has already given a standing default account. Once a user states a default, treat it as standing across sessions (not just within one conversation) until they change it — don't re-ask every time.
- Present monetary values with proper formatting (e.g., `$1,234.56`). Present percentages to two decimal places (e.g., `12.34%`).
- When presenting stock data, always include the symbol, current price, and relevant context (e.g., day change).

## Custom Baskets
- Custom baskets (user-defined indices) are stored locally, in `~/.tradethos`. They are not
  Robinhood Watchlists and need no network call to read or write.
- One append-only event log, `~/.tradethos/events.log.jsonl`, is the source of truth for every
  basket's name, target weights, thesis text, and trade history. Every read replays this log.
- `~/.tradethos/baskets/<slug>.json` holds one snapshot file per basket. A snapshot is an
  export for the user to read. No command reads a snapshot back.
- `basket.py`, the basket-manager skill's command-line tool, is the only writer. The agent
  never edits a basket file directly.
- `record-fills` is the only command that records a trade, and it reads the share count and
  the fill price from a real Robinhood order — never from a number the agent typed.
- `basket.py verify --positions` compares a basket's claimed shares to the real account
  position. Run it when reporting a basket, and offer the repair when a symbol is
  over-claimed (see the basket-manager skill).

## Research-First Approach
- When a user expresses interest in buying a stock they haven't researched yet, suggest running research first before proceeding to trade.
- After research, offer to add the stock to a custom basket if the user expresses positive sentiment.

## Safety Rails
- For trade execution, always call `review_equity_order` before `place_equity_order` unless the user has **very explicitly** asked to skip the review (e.g., "skip the review", "just place it").
- A generic "place this order" or "buy AAPL" is **NOT** a review bypass.
- Always use `get_equity_tradability` to verify a symbol is tradable before attempting an order.
- Generate a fresh UUID `ref_id` for each new logical order. Reuse the same `ref_id` only when retrying a failed transport.

## Writing Plans and Documentation
- When you write plans or documentation, use ASD-STE100 Simplified Technical English (STE).

## Skill Coordination
- Skills can reference each other. For example, the trade-executor skill can read basket data via `basket.py show` to suggest basket-aligned trades.
- When suggesting cross-skill actions (e.g., "would you like to add this to a basket?"), frame them as offers, not automatic actions.
- After a trade fills for a stock in a basket, offer to record the fill. The record goes through `basket.py record-fills`, passing the order id — never a typed share count or price.
