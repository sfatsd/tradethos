# Tradethos 📈

**Tradethos** is a multi-platform agent plugin for building and tracking custom stock baskets, user-defined indexes, and ETF-style portfolios. It uses the Robinhood Model Context Protocol (MCP) for market data and brokerage workflows.

Works with [Claude Code](#claude-code), [Cursor](#cursor), and [Codex](#codex).

---

## Key Features

- **Personal Stock Baskets**: Create and track thematic stock indices (e.g., *AI Infrastructure*, *Storage & Memory*, *Optical & Photonics*) with custom target allocations.
- **Transaction & Position Tracking**: Track actual positions, share counts, average cost basis, realized/unrealized P&L, and daily/total changes.
- **Safety-First Trade Execution**: Mandatory review-before-place safety rails for all brokerage operations, ensuring user confirmation prior to order placement.
- **Deep Market & Fundamental Research**: Full suite of fundamental analysis, financial statement trends, technical indicators (RSI, MACD, Bollinger Bands, Moving Averages), and earnings history.
- **Stock Screening**: Build and execute live market scanners using Robinhood's Beacon screening engine.
- **Privacy-First Local Storage**: All personal portfolio data and transaction histories are saved locally under `data/baskets/` and kept out of version control (`.gitignore`).

---

## Included Agent Skills

Tradethos ships five specialized skills in `skills/`:

| Skill | Description | Primary MCP Tools Used |
|---|---|---|
| **🧺 `basket-manager`** | Create, edit, and manage custom thematic stock baskets with target weights, transaction logs, and average cost calculations. | Local JSON storage under `data/baskets/` |
| **📊 `stock-researcher`** | Conduct structured stock & industry research covering valuation, financials, technical indicators, and earnings. | `get_equity_fundamentals`, `get_financials`, `get_equity_technical_indicators`, `get_earnings_results` |
| **💰 `trade-executor`** | Review and place market, limit, and stop orders with mandatory safety checks, extended hours handling, and idempotency protection. | `review_equity_order`, `place_equity_order`, `get_equity_tradability`, `cancel_equity_order` |
| **📈 `portfolio-tracker`** | Monitor open positions, evaluate total account P&L, analyze basket target drift, and highlight rebalancing needs. | `get_portfolio`, `get_equity_positions`, `get_equity_quotes`, `get_realized_pnl` |
| **🔍 `stock-screener`** | Discover investment opportunities by creating and running custom screeners and market presets. | `create_scan`, `run_scan`, `get_scanner_filter_specs` |

---

## Repository Structure

Following the [superpowers](https://github.com/obra/superpowers) plugin layout — skills live at the repo root and each agent platform reads them via its own plugin manifest:

```
tradethos/
├── .agents/
│   └── plugins/
│       └── marketplace.json          # Codex marketplace manifest
├── .claude-plugin/
│   ├── plugin.json                   # Claude Code plugin manifest
│   └── marketplace.json              # Claude marketplace manifest
├── .cursor-plugin/
│   └── plugin.json                   # Cursor plugin manifest
├── .codex-plugin/
│   └── plugin.json                   # Codex plugin manifest
├── skills/                           # Single source of truth for all skills
│   ├── basket-manager/
│   │   ├── SKILL.md
│   │   └── examples/sample_basket.json
│   ├── stock-researcher/
│   │   ├── SKILL.md
│   │   └── references/research_workflow.md
│   ├── trade-executor/
│   │   ├── SKILL.md
│   │   └── references/order_rules.md
│   ├── portfolio-tracker/
│   │   ├── SKILL.md
│   │   └── references/tracking_guide.md
│   └── stock-screener/
│       ├── SKILL.md
│       └── references/filter_guide.md
├── data/
│   └── baskets/                      # Local basket JSON files (gitignored)
├── config.json                       # Shared defaults and thresholds
├── AGENTS.md                         # Project rules & safety guidelines
├── LICENSE
└── README.md
```

---

## Getting Started

### Prerequisites

- A Robinhood MCP server installed and configured in your environment. Tradethos does not include brokerage credentials or an MCP server.

### Installation status

Tradethos is **not published** to the Cursor, Claude, or Codex official plugin marketplaces yet. Users can still install it today from the public GitHub repo (Codex and Claude Code) or via a local install (Cursor).

| Platform | Works today without publishing? | How |
|---|---|---|
| **Codex** | Yes | Register this GitHub repo as a marketplace source |
| **Claude Code** | Yes | Register this GitHub repo as a marketplace source |
| **Cursor** | Local install only | Symlink the cloned repo into `~/.cursor/plugins/local/` |

After Tradethos is published, one-click marketplace installs (`/add-plugin tradethos`, etc.) will work too.

### Codex

Register the GitHub repo as a marketplace, then install the plugin:

```bash
codex plugin marketplace add sfatsd/tradethos --ref main
codex plugin add tradethos@sfatsd
```

For local development from a checkout:

```bash
git clone https://github.com/sfatsd/tradethos.git
cd tradethos
codex plugin marketplace add .
codex plugin add tradethos@sfatsd
```

Start a new Codex task after installation so its skills are loaded.

### Claude Code

In a Claude Code session, register the GitHub repo and install:

```bash
/plugin marketplace add sfatsd/tradethos
/plugin install tradethos@sfatsd
/reload-plugins
```

For local development from a checkout:

```bash
git clone https://github.com/sfatsd/tradethos.git
cd tradethos
/plugin marketplace add .
/plugin install tradethos@sfatsd
/reload-plugins
```

If install fails with an SSH `Permission denied (publickey)` error, force Git to use HTTPS:

```bash
git config --global url."https://github.com/".insteadOf "git@github.com:"
```

### Cursor

Cursor marketplace search and `/add-plugin tradethos` only work **after** the plugin is submitted and approved at [cursor.com/marketplace/publish](https://cursor.com/marketplace/publish).

Until then, install from a local clone:

```bash
git clone https://github.com/sfatsd/tradethos.git
ln -s "$(pwd)/tradethos" ~/.cursor/plugins/local/tradethos
```

Restart Cursor. The plugin loads from `.cursor-plugin/plugin.json` and the skills in `skills/`.

To remove the local install:

```bash
rm ~/.cursor/plugins/local/tradethos
```

### Clone the source

```bash
git clone https://github.com/sfatsd/tradethos.git
```

### Usage Examples

Simply interact with your AI agent naturally:

- **Create a Basket**: *"Create a basket called Storage Leaders with WDC at 30%, STX at 30%, and MU at 40%"*
- **Research a Stock**: *"Research NVDA for me and check its 14-day RSI and recent quarterly revenue growth"*
- **Screen for Opportunities**: *"Screen for stocks with RSI below 30 and market cap over $10B"*
- **Check Basket Performance**: *"How is my Storage Leaders basket performing today?"*
- **Execute & Log a Trade**: *"Buy 5 shares of WDC and record the transaction in my Storage Leaders basket"*

---

## Safety & Guardrails

- **Explicit User Confirmation**: All order execution (`place_equity_order`), cancellations, and watchlist modifications require explicit user approval.
- **Simulated Order Reviews**: Orders are automatically run through `review_equity_order` to display estimated cost, purchasing power, and warnings before placement.
- **Idempotency**: Orders generate unique client UUIDs (`ref_id`) to prevent accidental duplicate orders on transport retries.

See [AGENTS.md](AGENTS.md) for the full project rules.

---

## License

This project is licensed under the [MIT License](LICENSE) - © 2026 Wei Yuan Shih.
