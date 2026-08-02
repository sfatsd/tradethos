# Changelog

All notable changes to Tradethos are documented here. Versions follow
[Semantic Versioning](https://semver.org/): breaking changes bump MINOR
while the project is pre-1.0, fixes bump PATCH.

## [0.3.0] — Local basket storage

Custom baskets moved out of Robinhood watchlist descriptions and into a local
append-only event log at `~/.tradethos`. The old `Z64:` cloud format packed
basket metadata into a 256-character watchlist field; measured against real
data it held 7 symbols before hitting the limit, not the 30+ originally
assumed — compression cannot help a payload this small and this high-entropy.

**Storage**
- One append-only event log (`events.log.jsonl`) is now the source of truth
  for every basket's name, target weights, thesis text, and trade history.
  Every read replays it; there is no cache to go stale.
- Snapshot files under `baskets/<slug>.json` are exports for the user to
  read — no command reads a snapshot back.
- `basket.py` is the only writer, with 19 subcommands (`create`, `show`,
  `set-weight`, `record-fills`, `verify`, `backup`, and more).
- Deleted the old cloud-native scripts: `basket_utils.py`,
  `basket_summary.py`, `list_symbols.py`, `migrate_to_watchlists.py`.

**Safety invariants**
- Target weights are whole numbers that always sum to exactly 100,
  normalized by largest remainder with an alphabetical tie-break.
- `record-fills` is the only path a trade takes into the log — it reads
  `average_price` from a real Robinhood order and accepts no typed share
  count or price.
- `basket.py verify --positions` compares a basket's claimed shares against
  the real account position and flags over-claims.

**Migration**
- `migrate_v2.py` moves existing cloud baskets into the local store in one
  pass. Already run against the live account: six baskets, 86 events, zero
  over-claims on verification. Order data is treated as authoritative over
  the old watchlist snapshots, which disagreed with real fills by up to
  0.0001 shares.

**Known gaps**
- `verify <slug>` still returns no row for a symbol the named basket
  *targets* but holds none of — the unfiltered `verify --positions` always
  catches it.
- The unfiltered over-claim warning doesn't name which baskets are involved.
- Floats are used rather than `Decimal`; realized P&L ignores fees; no
  corporate-action (stock split) handling.

## [0.2.2]

- Repaired the cloud-native basket path and added CI.

## [0.2.1] — Z64 compression

- Added `zlib` + Base64 (`Z64:`) compression for basket metadata stored in
  Robinhood watchlist descriptions, plus recovery tools.

## [0.2.0] — Cloud-Native Watchlist Basket Management

- Initial cloud-native basket storage: baskets saved as Robinhood Watchlists
  named `Basket: <Name>`, with metadata encoded into the watchlist
  description field.
