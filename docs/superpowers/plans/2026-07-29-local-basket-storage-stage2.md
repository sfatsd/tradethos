# Local Basket Storage — Stage 2: Migration

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the user's six live cloud baskets into the local event store, taking share counts and prices from Robinhood order data and using the old `Z64:` snapshots only to decide which basket owns which order.

**Architecture:** One new script, `migrate_v2.py`. It reads three inputs as JSON on the command line — the `get_watchlists` response, the `get_equity_orders` response, and the legacy `data/baskets/*.json` files — and writes events through `basket_events.py`. It never writes the log itself and never touches the cloud.

**Tech Stack:** Python 3.9+, standard library only, `unittest`.

**Spec:** `docs/superpowers/specs/2026-07-27-basket-local-storage-design.md` §8. **Stage 1 plan:** `docs/superpowers/plans/2026-07-28-local-basket-storage-stage1.md`.

## Global Constraints

- Python 3.9 floor. No `match`, no `X | Y` unions, no builtin generics in annotations.
- Standard library only. Tests use `unittest`, not pytest.
- **The migration writes through `basket_events.py`.** It never opens `events.log.jsonl` itself. §8.4.
- **`--dry-run` is the default. `--apply` writes.** §8.3.
- **The migration never modifies or deletes a watchlist.** The cloud data stays exactly as it is. §8.3.
- **Order data is authoritative for shares and prices.** The `Z64:` snapshot is a lossy 5-decimal copy used only for attribution. This corrects §8.2, which assumed the two agree exactly — measured against live data, they differ by up to 0.0001.
- Target weights are whole numbers summing to exactly 100. Legacy weights must be normalized.
- Never invoke any CLI without `--data-dir` pointing at a temp directory; the default is the user's real `~/.tradethos`.

## What the live data actually looks like

Measured before writing this plan. Six baskets exist, not four:

| Watchlist | Slug in metadata | Symbols | Snapshot | Weight sum |
|---|---|---|---|---|
| Basket: Magnificent 7 Index | `magnificent-7-index` | 7 | 7 | 100 |
| Basket: Optical & Photonics Index | `optical-and-photonics-in` (truncated) | 6 | 6 | 100 |
| Basket: Semiconductor ETF-Style Basket | `semiconductor-etf-style` | 10 | 0 | 100 |
| Basket: Storage & Memory Index | `storage-and-memory-index` | 6 | 6 | 100 |
| Test 250 Char Limit Basket | `test-250` | 12 | 0 | **120** |
| Test 20 Symbol Compressed Basket | `ai-20` | 20 | 20 | 100 |

Four facts this plan must handle, none of them in the spec:

1. **`test-250`'s weights sum to 120.** Twelve holdings at 10 each. Normalization is mandatory, not cosmetic.
2. **`optical-and-photonics-in` is a truncated slug**, cut at the old 24-character limit. The new store has no such limit.
3. **Two baskets have metadata but zero watchlist items.** Membership comes from the metadata's weight keys, not from `get_watchlist_items`.
4. **Snapshots and orders disagree slightly.** Measured differences: IPGP 0.05193 vs 0.051824, AAOI 0.08898 vs 0.088928, SPCX 0.1225 vs 0.12247. The orders are right.

## Attribution, resolved against the real data

Three symbols sit in more than one basket: **NVDA**, **MU**, **LITE**. All three resolve automatically by matching each order's filled quantity to the closest snapshot claim:

- **LITE** — orders 0.029940 and 0.011975. Optical claims 0.02996, Storage claims 0.01198. Each order matches one basket.
- **MU** — one order 0.020247. Storage claims 0.02024. Semiconductor has no snapshot, so it claims nothing.
- **NVDA** — orders 0.068659 (23 July) and 0.048067 (27 July). Mag7 claims 0.06865, matching the first. Semiconductor claims nothing. **The second order matches no claim and must be reported as unattributed** — it was a standalone purchase, and leaving it out is correct.

---

## File Structure

| File | Responsibility |
|---|---|
| `skills/basket-manager/scripts/migrate_v2.py` | Read the three sources, attribute orders, build events, write through `basket_events`. |
| `tests/fixtures/live_orders.json` | Already committed. The 20 filled orders, captured live. |
| `tests/fixtures/live_watchlists.json` | The `get_watchlists` response, captured live. Created in Task 1. |
| `tests/test_migrate_v2.py` | Attribution, normalization, and end-to-end migration tests. |

---

## Task 1: Capture the watchlist fixture and build the attribution engine

**Files:**
- Create: `tests/fixtures/live_watchlists.json`
- Create: `skills/basket-manager/scripts/migrate_v2.py`
- Test: `tests/test_migrate_v2.py`

**Interfaces:**
- Consumes: `basket_utils.decode_watchlist_metadata` (the OLD module, still present — it is deleted in Stage 3).
- Produces:
  - `SHARE_TOLERANCE = 0.0005`
  - `read_baskets(watchlists_response) -> list` of dicts with `slug`, `name`, `weights`, `snapshot`, `threshold`
  - `attribute_orders(baskets, orders) -> (assignments, unattributed)` where `assignments` maps `order_id` to a slug
  - `normalize_basket_weights(weights) -> (normalized, changed)`

**Attribution rule.** For each filled order, find every basket whose weight set contains the symbol AND whose snapshot claims a non-zero share count for it. Score each candidate by `abs(order_shares - claimed_shares)`. Assign the order to the best candidate when that difference is within `SHARE_TOLERANCE` and no other order has already claimed it. An order matching no candidate within tolerance is unattributed and reported. A basket with no snapshot entry for a symbol claims nothing and never wins.

`SHARE_TOLERANCE` is 0.0005 — an order of magnitude above the largest observed drift (IPGP, 0.000106) and far below the smallest gap between competing claims (LITE, 0.018).

- [ ] **Step 1: Capture the watchlist fixture**

Run this to write the fixture from the values measured live. Paste the six `display_description` strings exactly as they appear in `get_watchlists`; they are reproduced in the Stage 2 dispatch notes.

```bash
mkdir -p tests/fixtures
```

Write `tests/fixtures/live_watchlists.json` containing the full `get_watchlists` response envelope — `{"data": {"watchlists": [...]}}` — with all nine watchlists, including the three non-basket lists (`My First List`, `Cryptos to Watch`, `Options Watchlist`) so the tests prove those are skipped.

- [ ] **Step 2: Write the failing test**

Create `tests/test_migrate_v2.py`:

```python
#!/usr/bin/env python3
"""Tests for the Stage 2 migration."""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "basket-manager" / "scripts"))

from migrate_v2 import (
    SHARE_TOLERANCE,
    attribute_orders,
    normalize_basket_weights,
    read_baskets,
)

FIXTURES = ROOT / "tests" / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


class TestReadBaskets(unittest.TestCase):

    def setUp(self):
        self.baskets = read_baskets(load("live_watchlists.json"))
        self.by_slug = dict((b["slug"], b) for b in self.baskets)

    def test_finds_every_basket_and_skips_ordinary_lists(self):
        self.assertEqual(len(self.baskets), 6)
        self.assertNotIn("my-first-list", self.by_slug)

    def test_reads_weights_and_snapshots(self):
        mag7 = self.by_slug["magnificent-7-index"]
        self.assertEqual(len(mag7["weights"]), 7)
        self.assertEqual(len(mag7["snapshot"]), 7)

    def test_a_basket_without_a_snapshot_reads_as_definition_only(self):
        semi = self.by_slug["semiconductor-etf-style"]
        self.assertEqual(len(semi["weights"]), 10)
        self.assertEqual(semi["snapshot"], {})

    def test_a_basket_with_no_watchlist_items_still_reads(self):
        # test-250 and ai-20 have item_count 0 but carry full metadata.
        self.assertIn("test-250", self.by_slug)
        self.assertEqual(len(self.by_slug["test-250"]["weights"]), 12)


class TestNormalizeBasketWeights(unittest.TestCase):

    def test_a_weight_set_that_sums_to_120_is_normalized(self):
        weights = dict(("S%d" % i, 10) for i in range(12))
        normalized, changed = normalize_basket_weights(weights)
        self.assertEqual(sum(normalized.values()), 100)
        self.assertTrue(changed)

    def test_an_already_whole_set_reports_no_change(self):
        normalized, changed = normalize_basket_weights({"A": 60, "B": 40})
        self.assertEqual(normalized, {"A": 60, "B": 40})
        self.assertFalse(changed)

    def test_fractional_weights_become_whole_numbers(self):
        normalized, _ = normalize_basket_weights({"A": 14.29, "B": 14.29, "C": 71.42})
        self.assertEqual(sum(normalized.values()), 100)
        for value in normalized.values():
            self.assertEqual(value, int(value))


class TestAttribution(unittest.TestCase):

    def setUp(self):
        self.baskets = read_baskets(load("live_watchlists.json"))
        self.orders = load("live_orders.json")["data"]["orders"]
        self.assignments, self.unattributed = attribute_orders(self.baskets, self.orders)

    def slug_for(self, symbol, shares):
        for order in self.orders:
            if order["symbol"] == symbol and abs(float(order["cumulative_quantity"]) - shares) < 1e-9:
                return self.assignments.get(order["id"])
        self.fail("no order for %s %s" % (symbol, shares))

    def test_lite_orders_split_across_two_baskets(self):
        # 0.029940 matches Optical's 0.02996; 0.011975 matches Storage's 0.01198.
        self.assertEqual(self.slug_for("LITE", 0.029940), "optical-and-photonics-in")
        self.assertEqual(self.slug_for("LITE", 0.011975), "storage-and-memory-index")

    def test_mu_goes_to_storage_because_semiconductor_claims_nothing(self):
        self.assertEqual(self.slug_for("MU", 0.020247), "storage-and-memory-index")

    def test_the_first_nvda_order_goes_to_magnificent_seven(self):
        self.assertEqual(self.slug_for("NVDA", 0.068659), "magnificent-7-index")

    def test_the_standalone_nvda_order_is_unattributed(self):
        # Bought 27 July outside any basket. Leaving it out is correct.
        self.assertIsNone(self.slug_for("NVDA", 0.048067))
        ids = [o["id"] for o in self.unattributed]
        self.assertIn("REDACTEDC5-0000-0000-0000-000000000023", ids)

    def test_exactly_one_order_is_unattributed(self):
        self.assertEqual(len(self.unattributed), 1)

    def test_every_other_order_is_assigned(self):
        assigned = [o for o in self.orders if self.assignments.get(o["id"])]
        self.assertEqual(len(assigned), 19)

    def test_no_order_is_assigned_to_two_baskets(self):
        self.assertEqual(len(set(self.assignments)), len(self.assignments))

    def test_a_basket_without_a_snapshot_receives_no_orders(self):
        owned = [s for s in self.assignments.values() if s == "semiconductor-etf-style"]
        self.assertEqual(owned, [])

    def test_tolerance_absorbs_the_observed_drift(self):
        # IPGP is the worst real case: claimed 0.05193, filled 0.051824.
        self.assertGreater(SHARE_TOLERANCE, 0.000106)
        self.assertEqual(self.slug_for("IPGP", 0.051824), "optical-and-photonics-in")

    def test_tolerance_is_tight_enough_to_separate_competing_claims(self):
        # LITE's two claims differ by 0.018; the tolerance must be far below that.
        self.assertLess(SHARE_TOLERANCE, 0.018 / 2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run to verify it fails**

Run: `python3 -m unittest tests.test_migrate_v2 -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'migrate_v2'`

- [ ] **Step 4: Implement `read_baskets`, `normalize_basket_weights`, and `attribute_orders`**

Create `skills/basket-manager/scripts/migrate_v2.py`. Import `decode_watchlist_metadata` from `basket_utils` and `normalize_weights` from `basket_weights`. Implement:

- `read_baskets(response)` — unwrap `{"data": {"watchlists": [...]}}`, decode each `display_description`, skip any that does not decode, and return one dict per basket with `slug`, `name` (the `display_name` minus a leading `"Basket: "`), `weights`, `snapshot` (the `h` map, or `{}`), and `threshold`.
- `normalize_basket_weights(weights)` — call `basket_weights.normalize_weights`, and return the result with a boolean saying whether any value changed.
- `attribute_orders(baskets, orders)` — implement the rule stated in this task's Interfaces section. Consider only orders whose `state` is `filled`. Take share counts from `cumulative_quantity`, falling back to `quantity`. Assign each order at most once, best match first, so that a closer match wins a contested claim.

- [ ] **Step 5: Run the tests**

Run: `python3 -m unittest tests.test_migrate_v2 -v`
Expected: PASS, 17 tests.

- [ ] **Step 6: Run the whole suite and commit**

```bash
python3 -m unittest discover -s tests
git add skills/basket-manager/scripts/migrate_v2.py tests/test_migrate_v2.py tests/fixtures/
git commit -m "feat(migrate): attribute live orders to baskets

Order data is authoritative for shares and prices; the Z64 snapshot
decides only which basket owns an order. Measured against the live
account, the two differ by up to 0.0001, so the tolerance is 0.0005."
```

---

## Task 2: Build the events and write them

**Files:**
- Modify: `skills/basket-manager/scripts/migrate_v2.py`
- Modify: `tests/test_migrate_v2.py`

**Interfaces:**
- Consumes: `basket_events.EventLog`, `basket_events.make_event`; `basket_store.replay`, `basket_store.slugify`.
- Produces:
  - `read_legacy_theses(baskets_dir) -> dict` mapping slug to `{symbol: thesis}` plus `{"__description__": str}`
  - `build_events(baskets, assignments, orders, theses) -> list`
  - `migrate(data_dir, watchlists, orders, legacy_dir, apply=False) -> dict` (the report)

**Slug rule.** Regenerate each slug from the display name with `basket_store.slugify`, ignoring the metadata's stored slug. The old format truncated at 24 characters — `optical-and-photonics-in` — and the new store has no such limit. Record both the old and new slug in the report so the change is visible.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_migrate_v2.py`:

```python
import tempfile

from basket_events import EventLog
from basket_store import replay


class TestBuildAndMigrate(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.watchlists = load("live_watchlists.json")
        self.orders = load("live_orders.json")

    def tearDown(self):
        self.tmp.cleanup()

    def run_migration(self, apply=True):
        from migrate_v2 import migrate
        return migrate(self.dir, self.watchlists, self.orders, None, apply=apply)

    def state(self):
        return replay(EventLog(self.dir).read()).baskets

    def test_dry_run_writes_nothing(self):
        report = self.run_migration(apply=False)
        self.assertFalse((self.dir / "events.log.jsonl").exists())
        self.assertTrue(report["dry_run"])
        self.assertEqual(len(report["baskets"]), 6)

    def test_apply_creates_every_basket(self):
        self.run_migration()
        self.assertEqual(len(self.state()), 6)

    def test_the_truncated_slug_is_regenerated(self):
        self.run_migration()
        slugs = set(self.state())
        self.assertIn("optical-photonics-index", slugs)
        self.assertNotIn("optical-and-photonics-in", slugs)

    def test_weights_are_normalized_to_one_hundred(self):
        self.run_migration()
        for slug, basket in self.state().items():
            total = sum(h.target_weight_pct for h in basket.holdings.values())
            self.assertEqual(total, 100, slug)
            for holding in basket.holdings.values():
                self.assertEqual(holding.target_weight_pct,
                                 int(holding.target_weight_pct))

    def test_the_one_hundred_and_twenty_basket_is_reported_as_changed(self):
        report = self.run_migration()
        entry = [b for b in report["baskets"] if b["old_slug"] == "test-250"][0]
        self.assertTrue(entry["weights_changed"])

    def test_positions_come_from_order_data_not_the_snapshot(self):
        self.run_migration()
        mag7 = self.state()["magnificent-7-index"]
        nvda = mag7.holdings["NVDA"].position
        # The order filled 0.068659; the lossy snapshot said 0.06865.
        self.assertAlmostEqual(nvda.shares, 0.068659, places=9)
        self.assertAlmostEqual(nvda.avg_cost, 208.1299, places=4)

    def test_the_unattributed_order_is_in_no_basket(self):
        self.run_migration()
        total = 0.0
        for basket in self.state().values():
            holding = basket.holdings.get("NVDA")
            if holding:
                total += holding.position.shares
        self.assertAlmostEqual(total, 0.068659, places=9)

    def test_the_report_names_the_unattributed_order(self):
        report = self.run_migration()
        self.assertEqual(len(report["unattributed"]), 1)
        self.assertEqual(report["unattributed"][0]["symbol"], "NVDA")

    def test_a_second_apply_changes_nothing(self):
        self.run_migration()
        before = EventLog(self.dir).count()
        self.run_migration()
        self.assertEqual(EventLog(self.dir).count(), before)

    def test_the_migration_writes_through_the_event_log(self):
        # Every line must be a valid event with a version field.
        self.run_migration()
        for event in EventLog(self.dir).read():
            self.assertIn("v", event)
            self.assertIn("type", event)
            self.assertIn("slug", event)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest tests.test_migrate_v2.TestBuildAndMigrate -v`
Expected: FAIL — `migrate` does not exist.

- [ ] **Step 3: Implement `build_events` and `migrate`**

`build_events` emits, per basket, in this order: one `basket_created` (name, description, account, threshold), one `holding_added` per symbol carrying its NORMALIZED weight, then one `buy` per attributed order in fill-time order, carrying `shares`, `price` from `average_price`, `amount`, `order_id`, and `ts` from `last_transaction_at`.

`migrate` builds the events, and when `apply` is true appends them through `EventLog`. When `apply` is false it writes nothing. It returns a report with `dry_run`, `baskets` (each with `old_slug`, `new_slug`, `symbols`, `orders`, `weights_changed`), `unattributed`, and `warnings`.

**Idempotency:** before writing, replay the existing log. Skip any basket whose slug already exists, and skip any order whose id already appears. A second `--apply` must add nothing.

- [ ] **Step 4: Run the tests, then the suite, then commit**

```bash
python3 -m unittest tests.test_migrate_v2 -v
python3 -m unittest discover -s tests
git add skills/basket-manager/scripts/migrate_v2.py tests/test_migrate_v2.py
git commit -m "feat(migrate): build and write the migration events

Slugs regenerate from the display name, dropping the old 24-character
truncation. Weights normalize to 100. A second apply is a no-op."
```

---

## Task 3: The command-line wrapper

**Files:**
- Modify: `skills/basket-manager/scripts/migrate_v2.py`
- Modify: `tests/test_migrate_v2.py`

**Interfaces:**
- Produces: `main(argv) -> int`, with `--watchlists-json`, `--orders-json`, `--legacy-dir`, `--data-dir`, `--apply`, `--format {json,table}`.

- [ ] **Step 1: Write the failing test**

Append a `TestMigrateCli` class that runs `migrate_v2.py` as a subprocess against a temp `--data-dir`, and asserts: `--dry-run` is the default with no `--apply` flag given and writes no log; `--apply` writes; the table format prints a human-readable summary naming the unattributed order; and a missing required argument exits 1 with a JSON error envelope on stderr.

- [ ] **Step 2: Implement `main`**

Mirror `basket.py`'s conventions exactly: JSON on stdout by default, `--format table` for humans, errors as JSON on stderr, exit 0 success / 1 validation / 2 I/O. Print a prominent line when the run was a dry run, so nobody mistakes a preview for a completed migration.

- [ ] **Step 3: Run the tests, then the suite, then commit**

---

## Task 4: Migrate the live account

This task is run by the controller, not a subagent — it touches the user's real data.

- [ ] **Step 1: Dry run against live data**

Fetch `get_watchlists` and `get_equity_orders` for account `000000000`, pass both to `migrate_v2.py` with `--data-dir ~/.tradethos` and NO `--apply`, and read the report.

- [ ] **Step 2: Present the report to the user**

Show: the six baskets with their new slugs, the slug that changed, the basket whose weights were renormalized from 120, and the one unattributed NVDA order. Get explicit confirmation before applying.

- [ ] **Step 3: Apply, then verify**

Re-run with `--apply`, then run `basket.py verify --positions` against the live positions and confirm every symbol reports `match` or `outside_shares`. Any `over_claimed` means the migration is wrong — stop and investigate rather than proceeding.

- [ ] **Step 4: Leave the cloud untouched**

Confirm all six watchlists still exist unmodified. The user deletes them when they are satisfied, not the migration.
