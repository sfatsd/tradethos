---
name: stock-screener
description: >
  Create and run custom stock screeners (scanners) to discover investment opportunities using
  Robinhood's screening engine. Supports filtering by technical indicators (RSI, MACD),
  fundamentals (market cap, volume), and presets (daily gainers/losers, upcoming earnings).
  Use this skill when the user wants to find stocks matching specific criteria, screen for
  opportunities, or discover new investments.
---

# Stock Screener

Create and run custom stock screeners using Robinhood's scanning engine. Filter stocks by technical indicators, volume, market cap, and more to discover investment opportunities.

## When to Use This Skill

Trigger this skill when the user mentions:
- Screening or scanning for stocks
- Finding stocks that match certain criteria
- "Show me stocks with RSI above 70"
- "What stocks are moving today?"
- Daily gainers or losers
- "Find me cheap tech stocks"
- Stock discovery or opportunity hunting
- Upcoming earnings stocks

## General Rules & Standards

- **Formatting**: Present monetary values with proper formatting (e.g., `$1,234.56`). Present percentages to two decimal places (e.g., `12.34%`).
- **Stock Context**: Always include symbol, current price, and relevant market context when presenting scanner results.
- **User Confirmation**: Require explicit user confirmation before saving or modifying custom scans.
- **Dynamic Spec Discovery**: Always call `get_scanner_filter_specs` first to discover valid filter enums and predicates.

## Available Data Sources (Robinhood MCP)

| Tool | Purpose |
|---|---|
| `get_scanner_filter_specs` | Discover available filter types, predicates, and valid values |
| `create_scan` | Create a new saved screener with optional preset and custom filters |
| `run_scan` | Execute an existing screener and get live market results |
| `get_scans` | List the user's saved screeners |
| `update_scan_filters` | Modify filters on an existing screener |
| `update_scan_config` | Update screener configuration (title, sort, etc.) |

## Presets

Built-in screener presets available via `create_scan`:

| Preset | Value | Description |
|---|---|---|
| Daily Gainers | `DAILY_GAINERS` | Stocks with the largest % gain today |
| Daily Losers | `DAILY_LOSERS` | Stocks with the largest % loss today |
| High Options Volume/IV | `HIGH_OPTIONS_VOLUME_IV` | Stocks with elevated options activity |
| Upcoming Earnings | `UPCOMING_EARNINGS` | Stocks reporting earnings soon |
| Blank (custom only) | `INITIAL` | Empty scanner — must provide custom filters |

## Creating Custom Screeners

### Workflow

```
1. Understand criteria: What is the user looking for?
       ↓
2. Discover filters: Call get_scanner_filter_specs to see available options
       ↓
3. Build filters: Translate user criteria into filter objects
       ↓
4. Create scan: Call create_scan with preset, filters, and title
       ↓
5. Present results: Show matching stocks as a table
```

### Filter Object Structure

Each filter in the `filters` array has:

```json
{
  "filter_type": "FILTER_TYPE_RSI",
  "predicate": "PREDICATE_GREATER_THAN",
  "values": ["70"],
  "interval": "1d",
  "length": 14
}
```

| Field | Required | Description |
|---|---|---|
| `filter_type` | Yes | Wire-format enum (e.g., `FILTER_TYPE_RSI`) |
| `predicate` | Yes | Comparison operator (e.g., `PREDICATE_GREATER_THAN`) |
| `values` | Yes | Threshold values array |
| `interval` | Sometimes | Time granularity (e.g., `1d`) — required for time-series filters |
| `length` | Sometimes | Lookback period (e.g., `14` for RSI) — for indicators that need it |
| `plot` | Sometimes | Price field input (e.g., `open`, `close`) — for filters that support it |

### Important: Always Call `get_scanner_filter_specs` First

Before creating custom filters, call `get_scanner_filter_specs` to discover:
- Valid `filter_type` enum values
- Valid `predicate` values for each filter type
- Supported `interval` and `length` values
- Any constraints or limitations

Do **not** guess filter type or predicate values — they are wire-format enums that must be exact.

## Common Screening Recipes

### Overbought Stocks (RSI > 70)
```json
{
  "preset": "INITIAL",
  "title": "Overbought Stocks (RSI > 70)",
  "filters": [
    {
      "filter_type": "FILTER_TYPE_RSI",
      "predicate": "PREDICATE_GREATER_THAN",
      "values": ["70"],
      "interval": "1d",
      "length": 14
    }
  ]
}
```

### Oversold Stocks (RSI < 30)
```json
{
  "preset": "INITIAL",
  "title": "Oversold Stocks (RSI < 30)",
  "filters": [
    {
      "filter_type": "FILTER_TYPE_RSI",
      "predicate": "PREDICATE_LESS_THAN",
      "values": ["30"],
      "interval": "1d",
      "length": 14
    }
  ]
}
```

### High RSI + High Volume
```json
{
  "preset": "INITIAL",
  "title": "High RSI + High Volume",
  "filters": [
    {
      "filter_type": "FILTER_TYPE_RSI",
      "predicate": "PREDICATE_GREATER_THAN",
      "values": ["70"],
      "interval": "1d",
      "length": 14
    },
    {
      "filter_type": "FILTER_TYPE_VOLUME",
      "predicate": "PREDICATE_GREATER_THAN",
      "values": ["1000000"],
      "interval": "1d"
    }
  ]
}
```

## Running Existing Screeners

### View Saved Screeners
1. Call `get_scans` to list all saved scanners
2. Present: Scan ID, Title, Number of Filters
3. Ask which one to run

### Execute a Screener
1. Call `run_scan(scan_id)` to get live results
2. Results include: ticker, instrument_id, type, and column values
3. Present as a table with the scan's visible columns
4. Note: Results are real-time, not cached — each run reflects current market data

### Modify a Screener
1. Use `update_scan_filters` to change filter criteria
2. Use `update_scan_config` to change title, sort order, etc.
3. Re-run the scan to see updated results

## Presenting Results

When showing scan results:
1. Present as a formatted table
2. Note that results are **live market data** (not historical)
3. Show the total number of matching instruments
4. For the top results, offer follow-up actions:
   - "Would you like to research any of these stocks?" → stock-researcher skill
   - "Would you like to add any to a basket?" → basket-manager skill
   - "Would you like to buy any?" → trade-executor skill

## Cross-Skill Integration

- After screening, suggest the **stock-researcher** skill for deeper analysis on top candidates
- Offer to add screened stocks to a custom basket via the **basket-manager** skill
- If the user wants to act on a screener result, route to the **trade-executor** skill
- The **portfolio-tracker** skill can screen for drift opportunities (stocks that are underweight in a basket)

## Example Interactions

**User**: "What stocks are up the most today?"
→ Use preset `DAILY_GAINERS` → Create scan → Show top movers

**User**: "Find me oversold stocks with high volume"
→ Call `get_scanner_filter_specs` → Build RSI < 30 + Volume > 1M filter → Create scan → Show results

**User**: "Show me my saved screeners"
→ Call `get_scans` → List all saved scanners → Ask which to run

**User**: "Screen for stocks with upcoming earnings this week"
→ Use preset `UPCOMING_EARNINGS` → Create scan → Show results

**User**: "Find stocks with RSI below 30 and market cap over 10 billion"
→ Build multi-filter scan → Create → Show results → Offer research on top picks
