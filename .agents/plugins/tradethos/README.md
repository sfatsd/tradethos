# Tradethos

Tradethos helps you create and manage your own custom stock baskets, indexes, and ETF-style portfolios. It packages five finance workflows:

- **Stock Researcher** — fundamental, earnings, and technical research.
- **Stock Screener** — discover stocks using presets and market filters.
- **Basket Manager** — create and maintain model portfolios with target weights.
- **Portfolio Tracker** — inspect positions, P&L, and basket drift.
- **Trade Executor** — review, place, manage, and cancel equity orders with explicit confirmation.

## Requirements

Tradethos depends on an available Robinhood MCP connection that provides the market-data and brokerage tools named in each `SKILL.md`. The plugin does not include credentials or a brokerage connection.

## Safety

Order placement and cancellation require explicit confirmation. The trade workflow reviews an order before it is placed unless the user explicitly requests the review bypass.

## Data

Shared defaults are in `config.json`. Basket data is stored in `data/baskets/`; do not commit personal basket files.
