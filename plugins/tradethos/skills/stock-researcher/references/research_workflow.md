# Stock Research Workflow Reference

This document provides detailed workflows and examples for conducting stock research using the Robinhood MCP tools.

## Quick Reference: Tool Selection

| Question the User Asks | Primary Tool(s) |
|---|---|
| "What's the price of X?" | `get_equity_quotes` |
| "What does X do?" / "Tell me about X" | `search` → `get_equity_fundamentals` |
| "Is X expensive?" / "What's the P/E?" | `get_equity_fundamentals` |
| "How are X's revenues/earnings?" | `get_financials` |
| "Show me X's price chart" | `get_equity_historicals` |
| "What's the RSI/MACD/etc.?" | `get_equity_technical_indicators` |
| "When does X report earnings?" | `get_earnings_results` |
| "What earnings are coming up?" | `get_earnings_calendar` |
| "Is X a real company?" / "Find X" | `search` |

## Workflow: Full Standard Research

```
Step 1: Resolve the symbol
  └─ search(query="company name") → get symbol + instrument_id

Step 2: Fetch fundamentals + quote (parallel)
  ├─ get_equity_fundamentals(symbols=["SYM"])
  └─ get_equity_quotes(symbols=["SYM"])

Step 3: Fetch financials (parallel)
  ├─ get_financials(symbols=["SYM"], period="quarterly", limit=8)
  └─ get_financials(symbols=["SYM"], period="annual", limit=4)

Step 4: Fetch technicals (sequential — one indicator per call)
  ├─ get_equity_technical_indicators(symbol="SYM", type="sma", interval="day", period=50, start_time=1yr_ago, output="latest")
  ├─ get_equity_technical_indicators(symbol="SYM", type="sma", interval="day", period=200, start_time=1yr_ago, output="latest")
  ├─ get_equity_technical_indicators(symbol="SYM", type="rsi", interval="day", period=14, start_time=3mo_ago, output="latest")
  └─ get_equity_technical_indicators(symbol="SYM", type="macd", interval="day", start_time=6mo_ago, output="latest")

Step 5: Fetch earnings
  └─ get_earnings_results(symbol="SYM")

Step 6: Compile and present the research report
```

## Technical Indicator Interpretation Guide

### RSI (Relative Strength Index)
- **> 70**: Overbought — stock may be overextended, potential pullback
- **< 30**: Oversold — stock may be undervalued, potential bounce
- **40-60**: Neutral zone
- **Divergence**: If price makes new highs but RSI doesn't → bearish divergence

### MACD (Moving Average Convergence Divergence)
- **MACD line crosses above signal line**: Bullish crossover
- **MACD line crosses below signal line**: Bearish crossover
- **Histogram expanding**: Trend gaining momentum
- **Histogram contracting**: Trend losing momentum

### Moving Averages (SMA/EMA)
- **Price above SMA(200)**: Long-term uptrend
- **Price below SMA(200)**: Long-term downtrend
- **SMA(50) crosses above SMA(200)**: "Golden Cross" — bullish signal
- **SMA(50) crosses below SMA(200)**: "Death Cross" — bearish signal
- **EMA(20)**: Short-term trend direction

### Bollinger Bands
- **Price near upper band**: Potentially overbought
- **Price near lower band**: Potentially oversold
- **Band width expanding**: Increasing volatility
- **Band width contracting ("squeeze")**: Decreasing volatility, potential breakout

## Financial Metrics Interpretation

### Revenue Growth
- Calculate quarter-over-quarter and year-over-year growth rates
- Accelerating growth (growth rate increasing) is generally positive
- Decelerating growth doesn't necessarily mean decline — context matters

### Net Margin
- Compare against industry peers (not absolute thresholds)
- Expanding margins suggest improving efficiency or pricing power
- Compressing margins may indicate competitive pressure or investment phase

### P/E Ratio Context
- Compare against: the stock's own historical P/E, sector median, S&P 500 average
- High P/E may be justified by high growth — check PEG ratio (P/E ÷ earnings growth)
- Negative P/E means the company is unprofitable — look at revenue growth and path to profitability

## Time Range Recommendations

| Analysis Type | Start Time | Interval | Notes |
|---|---|---|---|
| Intraday trading | Today open | 5minute | Use `bounds=extended` for pre/post market |
| Short-term swing | 1 month ago | day | Default for "recent performance" |
| Medium-term trend | 6 months ago | day | Good for technical analysis |
| Long-term investing | 1-2 years ago | day or week | Pattern recognition |
| Historical context | 5 years ago | week or month | For "big picture" context |
