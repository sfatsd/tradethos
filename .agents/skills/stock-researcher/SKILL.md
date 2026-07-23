---
name: stock-researcher
description: >
  Deep-dive research and analysis on individual stocks and industries using Robinhood market data.
  Provides fundamental analysis (valuation, financials, earnings), technical analysis (indicators, price history),
  and structured research reports. Use this skill when the user wants to analyze a company before investing,
  compare stocks, or understand a company's financial health.
---

# Stock Researcher

Conduct comprehensive stock research using Robinhood MCP data. Produces structured research reports covering fundamentals, financials, technicals, and earnings to support investment decisions.

## When to Use This Skill

Trigger this skill when the user mentions:
- Researching or analyzing a stock or company
- Company fundamentals, valuation, or financials
- Technical analysis, indicators, charts, or price trends
- Earnings history or upcoming earnings
- Comparing companies or "which stock is better"
- "Tell me about [company]" in an investment context
- Industry or sector analysis

## General Rules & Standards

- **Formatting**: Present monetary values with proper formatting (e.g., `$1,234.56`). Present percentages to two decimal places (e.g., `12.34%`).
- **Stock Context**: When presenting stock research, always include the symbol, current price, and relevant day change context.
- **Research-First Approach**: When a user expresses interest in buying a stock they haven't analyzed, perform fundamental & technical research before offering to trade.
- **Cross-Skill Offers**: Frame cross-skill suggestions (e.g., "Would you like to add this stock to a basket or review an order?") as offers, never automatic actions.

## Available Data Sources (Robinhood MCP)

| Tool | Data Provided |
|---|---|
| `search` | Resolve company names to ticker symbols |
| `get_equity_fundamentals` | PE ratio, P/B, market cap, shares outstanding, float, 52-week range, dividend info, today's OHLCV |
| `get_financials` | Revenue, gross profit, net income, net margin — quarterly or annual, up to 40 periods |
| `get_equity_historicals` | OHLCV price bars across any time range with configurable intervals |
| `get_equity_technical_indicators` | RSI, MACD, Bollinger Bands, SMA, EMA, ATR, VWAP, OBV, ADX, momentum, and more |
| `get_earnings_results` | EPS history (up to 8 quarters), estimated vs actual, surprise analysis |
| `get_earnings_calendar` | Market-wide upcoming earnings, filterable by market cap |
| `get_equity_quotes` | Real-time price, bid/ask, day change |
| `get_equity_price_book` | Level 2 price book data |

## Research Report Structure

When conducting research, present findings in this structured format:

### 1. Company Overview
- Use `search` to resolve the company and get instrument details
- Use `get_equity_fundamentals` for company profile, market cap, sector
- Present: Name, Symbol, Market Cap, Sector/Industry, 52-week Range

### 2. Current Quote & Price Action
- Use `get_equity_quotes` for real-time pricing
- Use `get_equity_historicals` for recent price context (1-month daily bars)
- Present: Current Price, Day Change ($ and %), Volume vs Average, 52-week position

### 3. Valuation
- Use `get_equity_fundamentals` for valuation ratios
- Present: P/E Ratio, P/B Ratio, Market Cap, Shares Outstanding, Float
- Provide context: Is the stock trading at a premium or discount relative to historical norms?

### 4. Financial Performance
- Use `get_financials` with `period=quarterly` for last 8 quarters
- Use `get_financials` with `period=annual` for last 4 years
- Present: Revenue trend, Gross Profit trend, Net Income trend, Net Margin trend
- Calculate and highlight: YoY growth rates, margin expansion/compression

### 5. Technical Analysis
- Use `get_equity_technical_indicators` for key indicators:
  - **Trend**: SMA(50), SMA(200), EMA(20) — with daily interval over 1 year
  - **Momentum**: RSI(14) — daily interval over 3 months
  - **Volatility**: Bollinger Bands(20,2) — daily interval over 3 months
  - **Trend Strength**: MACD(12,26,9) — daily interval over 6 months
- Present: Current indicator values, buy/sell signals, trend direction
- Note: Always use `output=latest` for current readings, `output=last:5` for recent trend

### 6. Earnings
- Use `get_earnings_results` for EPS history
- Present: Last 4-8 quarters of EPS (estimated vs actual), beat/miss streak
- If earnings are upcoming, flag the date and timing (pre/post market)

### 7. Summary & Considerations
- Synthesize all sections into a brief bull/bear case
- Flag any risks: earnings approaching, high valuation, negative momentum
- **Do NOT make buy/sell recommendations** — present the data and let the user decide

## Research Depth Levels

### Quick Look (default for casual mentions)
- Sections 1 + 2 only
- Use when the user asks "what's AAPL at?" or "how's Tesla doing?"

### Standard Research (default for "research" or "analyze")
- Sections 1 through 6
- Use when the user explicitly asks to research or analyze a stock

### Deep Dive (when explicitly requested)
- All 7 sections with expanded data ranges
- Include longer historical data (5-year financials, 2-year technicals)
- Use when the user says "deep dive" or "full analysis"

## Comparative Analysis

When the user asks to compare two or more stocks:
1. Run the Standard Research for each stock
2. Present a comparison table with key metrics side-by-side:
   - Market Cap, P/E, Revenue Growth, Net Margin, RSI, 52-week performance
3. Highlight where each stock has an advantage
4. Do NOT declare a "winner" — present the data objectively

## Cross-Skill Integration

- After research, if the user is positive on the stock, offer to **add it to a custom basket** (basket-manager skill)
- If the user wants to act on research, suggest the **trade-executor** skill
- Suggest using the **stock-screener** skill to find similar companies in the same sector
- When a basket holding is being researched, note its target weight in the basket context

## Parameter Guidelines

### get_equity_historicals
- Always provide `start_time` in RFC3339 UTC format (e.g., `2026-01-01T00:00:00Z`)
- Let the server auto-select interval unless a specific granularity is needed
- Use `adjustment_type=split` for backtesting context

### get_equity_technical_indicators
- Always provide `interval` — it is required (unlike historicals where it's optional)
- Use `interval=day` for most analysis
- Use `output=latest` for current readings to minimize data
- Compute one indicator per call (tool limitation: one symbol per call)

### get_financials
- Default to `period=quarterly` with `limit=8` for recent trend
- Use `period=annual` with `limit=4` for longer-term view
- Up to 20 symbols per call for batch fundamentals

## Example Interactions

**User**: "Research NVDA for me"
→ Run Standard Research (sections 1-6), present structured report

**User**: "Compare AAPL and MSFT"
→ Run Standard Research on both, present comparison table

**User**: "What's the RSI on Tesla?"
→ Quick targeted call to `get_equity_technical_indicators` for RSI, present result

**User**: "How are GOOGL's earnings looking?"
→ Use `get_earnings_results` for GOOGL, present EPS history and next earnings date
