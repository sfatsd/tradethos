# Tradethos 📈

**Tradethos** is a portable, modular trading & investment skill package designed for AI agent platforms (such as OpenClaw, Antigravity, and MCP-compatible agent environments). It empowers AI agents to research equities, build and track custom stock baskets (user-defined indices), monitor performance, and safely execute brokerage operations via the Robinhood Model Context Protocol (MCP) server.

---

## 🌟 Key Features

- **Personal Stock Baskets**: Create and track thematic stock indices (e.g., *AI Infrastructure*, *Storage & Memory*, *Optical & Photonics*) with custom target allocations.
- **Transaction & Position Tracking**: Track actual positions, share counts, average cost basis, realized/unrealized P&L, and daily/total changes.
- **Safety-First Trade Execution**: Mandatory review-before-place safety rails for all brokerage operations, ensuring user confirmation prior to order placement.
- **Deep Market & Fundamental Research**: Full suite of fundamental analysis, financial statement trends, technical indicators (RSI, MACD, Bollinger Bands, Moving Averages), and earnings history.
- **Stock Screening**: Build and execute live market scanners using Robinhood's Beacon screening engine.
- **Privacy-First Local Storage**: All personal portfolio data and transaction histories are saved locally under `data/baskets/` and kept out of version control (`.gitignore`).

---

## 🧩 Included Agent Skills

This package includes 5 specialized skills inside `.agents/skills/`:

| Skill | Description | Primary MCP Tools Used |
|---|---|---|
| **🧺 `basket-manager`** | Create, edit, and manage custom thematic stock baskets with target weights, transaction logs, and average cost calculations. | Local JSON storage under `data/baskets/` |
| **📊 `stock-researcher`** | Conduct structured stock & industry research covering valuation, financials, technical indicators, and earnings. | `get_equity_fundamentals`, `get_financials`, `get_equity_technical_indicators`, `get_earnings_results` |
| **💰 `trade-executor`** | Review and place market, limit, and stop orders with mandatory safety checks, extended hours handling, and idempotency protection. | `review_equity_order`, `place_equity_order`, `get_equity_tradability`, `cancel_equity_order` |
| **📈 `portfolio-tracker`** | Monitor open positions, evaluate total account P&L, analyze basket target drift, and highlight rebalancing needs. | `get_portfolio`, `get_equity_positions`, `get_equity_quotes`, `get_realized_pnl` |
| **🔍 `stock-screener`** | Discover investment opportunities by creating and running custom screeners and market presets. | `create_scan`, `run_scan`, `get_scanner_filter_specs` |

---

## 📁 Repository Structure

```
tradethos/
├── .agents/
│   ├── AGENTS.md                                 # Project rules & safety guidelines
│   └── skills/
│       ├── basket-manager/                       # Custom basket management skill
│       │   ├── SKILL.md
│       │   └── examples/sample_basket.json
│       ├── stock-researcher/                     # Stock analysis & research skill
│       │   ├── SKILL.md
│       │   └── references/research_workflow.md
│       ├── trade-executor/                       # Order placement & safety skill
│       │   ├── SKILL.md
│       │   └── references/order_rules.md
│       ├── portfolio-tracker/                    # Position & P&L tracking skill
│       │   ├── SKILL.md
│       │   └── references/tracking_guide.md
│       └── stock-screener/                       # Custom screener & scanner skill
│           ├── SKILL.md
│           └── references/filter_guide.md
├── data/
│   └── baskets/                                  # Local storage for user basket JSON files (Gitignored)
│       └── .gitkeep
├── config.json                                   # Global configuration & default thresholds
├── .gitignore
├── LICENSE                                       # MIT License
└── README.md
```

---

## 🚀 Getting Started

### 1. Prerequisites
- An AI Agent platform supporting Agent Skills (`SKILL.md` format, e.g., OpenClaw, Antigravity).
- Robinhood MCP server installed and configured in your environment (see [Robinhood Agentic Trading Setup Guide](https://robinhood.com/us/en/support/articles/agentic-trading-overview/#ConnectyourAIagent)).

### 2. Installation
Clone this repository into your project directory or link `.agents/` to your agent workspace:

```bash
git clone https://github.com/sfatsd/tradethos.git
```

### 3. Usage Examples

Simply interact with your AI agent naturally:

- **Create a Basket**: *"Create a basket called Storage Leaders with WDC at 30%, STX at 30%, and MU at 40%"*
- **Research a Stock**: *"Research NVDA for me and check its 14-day RSI and recent quarterly revenue growth"*
- **Screen for Opportunities**: *"Screen for stocks with RSI below 30 and market cap over $10B"*
- **Check Basket Performance**: *"How is my Storage Leaders basket performing today?"*
- **Execute & Log a Trade**: *"Buy 5 shares of WDC and record the transaction in my Storage Leaders basket"*

---

## 🛡️ Safety & Guardrails

- **Explicit User Confirmation**: All order execution (`place_equity_order`), cancellations, and watchlist modifications require explicit user approval.
- **Simulated Order Reviews**: Orders are automatically run through `review_equity_order` to display estimated cost, purchasing power, and warnings before placement.
- **Idempotency**: Orders generate unique client UUIDs (`ref_id`) to prevent accidental duplicate orders on transport retries.

---

## 📄 License

This project is licensed under the [MIT License](LICENSE) - © 2026 Wei Yuan Shih.
