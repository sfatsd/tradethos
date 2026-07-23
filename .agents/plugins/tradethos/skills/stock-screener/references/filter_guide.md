# Stock Screener Filter Guide

Reference for filter types, predicates, and configuration options available in the Robinhood scanning engine.

## Important: Dynamic Filter Discovery

**Always call `get_scanner_filter_specs` before building custom filters.** The filter types, predicates, and valid values are defined by the server and may change. This document provides a general guide, but the live spec is authoritative.

## Filter Object Schema

```json
{
  "filter_type": "FILTER_TYPE_...",
  "predicate": "PREDICATE_...",
  "values": ["value1", "value2"],
  "interval": "1d",
  "length": 14,
  "plot": "close"
}
```

### Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `filter_type` | string | Yes | Wire-format enum identifying the metric |
| `predicate` | string | Yes | Comparison operator |
| `values` | string[] | Yes | Threshold values (count depends on predicate) |
| `interval` | string | Conditional | Time granularity for time-series filters |
| `length` | integer | Conditional | Lookback period for indicators |
| `plot` | string | Conditional | Price field (open, close, etc.) for applicable filters |

## Common Filter Types

> **Note**: These are illustrative. Always verify against `get_scanner_filter_specs`.

### Technical Indicators

| Filter Type | Description | Needs Interval? | Needs Length? |
|---|---|---|---|
| `FILTER_TYPE_RSI` | Relative Strength Index | Yes (`1d`) | Yes (default: 14) |
| `FILTER_TYPE_MACD` | MACD line value | Yes | Varies |
| `FILTER_TYPE_SMA` | Simple Moving Average | Yes | Yes |
| `FILTER_TYPE_EMA` | Exponential Moving Average | Yes | Yes |
| `FILTER_TYPE_BOLLINGER_BANDS` | Bollinger Band position | Yes | Yes |

### Volume & Price

| Filter Type | Description | Needs Interval? | Needs Length? |
|---|---|---|---|
| `FILTER_TYPE_VOLUME` | Trading volume | Yes (`1d`) | No |
| `FILTER_TYPE_PRICE` | Stock price | No | No |
| `FILTER_TYPE_PERCENT_CHANGE` | % price change | Yes | No |
| `FILTER_TYPE_MARKET_CAP` | Market capitalization | No | No |

### Fundamentals

| Filter Type | Description | Needs Interval? | Needs Length? |
|---|---|---|---|
| `FILTER_TYPE_PE_RATIO` | Price-to-Earnings ratio | No | No |
| `FILTER_TYPE_DIVIDEND_YIELD` | Dividend yield % | No | No |
| `FILTER_TYPE_EARNINGS_DATE` | Next earnings report date | No | No |

## Common Predicates

| Predicate | Operator | Values Count | Example |
|---|---|---|---|
| `PREDICATE_GREATER_THAN` | > | 1 | RSI > 70 |
| `PREDICATE_LESS_THAN` | < | 1 | RSI < 30 |
| `PREDICATE_EQUAL` | = | 1 | Exact match |
| `PREDICATE_BETWEEN` | range | 2 | Price between $10 and $50 |
| `PREDICATE_IN_LIST` | in | N | Sector in [Tech, Healthcare] |
| `PREDICATE_ANY_OF` | any | N | Multiple value match |

## Recipe: Translating User Intent to Filters

### "Find me cheap stocks"
→ Price filter < $20, or P/E < 15, or Market Cap < $2B (clarify with user)

### "High momentum stocks"
→ RSI > 60, or % Change > 5% (1d), or Price > SMA(50)

### "Undervalued stocks"
→ P/E < 15 + Market Cap > $1B (avoid penny stocks)

### "Stocks about to report earnings"
→ Use `UPCOMING_EARNINGS` preset, or Earnings Date filter

### "High volume movers"
→ Volume > 2x average + |% Change| > 3%

### "Dividend stocks"
→ Dividend Yield > 3% + Market Cap > $10B

## Multi-Filter Combinations

Filters are combined with AND logic — all conditions must be met. To build effective screeners:

1. **Start broad, then narrow**: Begin with 1-2 filters, see how many results match, add more to narrow
2. **Avoid over-filtering**: Too many filters may return zero results
3. **Mix technical + fundamental**: Combine RSI (technical) with Market Cap (fundamental) for balanced screening
4. **Consider the market context**: Overbought/oversold levels may need adjustment in trending markets

## Preset Screeners

| Preset | Best For |
|---|---|
| `DAILY_GAINERS` | Finding today's momentum leaders |
| `DAILY_LOSERS` | Finding potential oversold bounces or continued weakness |
| `HIGH_OPTIONS_VOLUME_IV` | Detecting unusual options activity (potential catalysts) |
| `UPCOMING_EARNINGS` | Planning around earnings events |
| `INITIAL` | Blank slate for fully custom screeners |

## Screener Management

### Saving: 
Screeners created via `create_scan` are automatically saved to the user's account.

### Updating:
- `update_scan_filters` — Replace all filters on an existing scan
- `update_scan_config` — Change title, sort order, visible columns

### Re-running:
Call `run_scan` with the same `scan_id` at any time for fresh results. Data is always live.

### Listing:
Call `get_scans` to see all saved screeners with their IDs and titles.
