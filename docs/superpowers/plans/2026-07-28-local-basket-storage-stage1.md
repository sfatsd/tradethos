# Local Basket Storage — Stage 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the local event-log store and its command-line tool, so a user can create baskets, change target weights, and record trades from Robinhood order data — with no skill changes and no risk to live data.

**Architecture:** One append-only JSONL event log at `~/.tradethos/events.log.jsonl` is the source of truth. Every read replays the log; there is no cache. Snapshot files under `~/.tradethos/baskets/` are exports that no command reads. Four modules: `basket_weights.py` (pure arithmetic), `basket_events.py` (log I/O and locking), `basket_store.py` (replay and money math), `basket.py` (CLI).

**Tech Stack:** Python 3.9+, standard library only. Tests use `unittest`. No pip dependencies.

**Spec:** `docs/superpowers/specs/2026-07-27-basket-local-storage-design.md` (committed at `7bf4cbb`). Section numbers below refer to it.

## Global Constraints

- **Python 3.9 is the floor.** CI runs 3.9, 3.10, 3.11, 3.12, 3.13. Do not use `match`, `X | Y` type unions, or `dict[str, int]` in annotations that get evaluated at runtime.
- **Standard library only.** No pip dependencies, ever. Import only from: `argparse`, `datetime`, `fcntl`, `hashlib`, `json`, `math`, `os`, `pathlib`, `re`, `shutil`, `sys`, `tempfile`, `time`, `unittest`.
- **Tests use `unittest`, not pytest.** The repo has no pytest. Run with `python3 -m unittest ...`.
- **New files go in `skills/basket-manager/scripts/`.** Tests go in `tests/`.
- **A stored target weight is a whole number. The weights of a basket always sum to exactly 100.** (§6.5)
- **Normalization uses the largest remainder method. A tie breaks by symbol, in alphabetical order.** (§6.5)
- **A stored weight is at least 1 and at most 100. A basket holds at most 100 holdings.** (§6.7)
- **The tool reads `average_price` from an order. It never reads the `price` field**, which holds the limit price. (§5.7)
- **The replay uses `shares` multiplied by `price`. It ignores the `amount` field.** (§5.7)
- **`order_id` is unique across all baskets. The replay ignores a repeated `order_id`; the first event wins; the replay reports every event it ignores.** (§5.7)
- **Exit codes:** 0 success, 1 validation error, 2 input or output error, 3 integrity error. (§9.4)
- **Errors print as JSON on stderr** with `error`, `code`, and `detail` keys. (§9.4)
- **Every write holds an exclusive `flock` across validation and append.** (§6.8)
- **Every event carries a `v` field.** The reader accepts every released version. (§9.5)
- **The data directory is a parameter with default `~/.tradethos`.** It is internal; the user does not configure it. Tests pass a temporary directory. (§10)

## Spec clarifications this plan assumes

The design leaves two points open. This plan resolves them as follows. Fold
the wording back into the spec before Stage 3 rewrites the skills.

**1. A taken `order_id` inside a batch skips, it does not abort.** §6.7 lists
"the same `order_id` in a different basket" as an exit-1 refusal, while §6.2
says a batch records its good fills and reports the rest. The exit-code table
in §6.2 already implies the answer: exit 1 belongs to the case where the tool
recorded nothing. This plan therefore skips the taken order with the reason
`ORDER_IN_OTHER_BASKET` and keeps the other fills. A single-order call still
exits 1, because it then records nothing.

**2. `--account` is required at `create`.** The design treats the account
check in `record-fills` as a safety rail, but a basket created without an
account stores an empty string, and the guard skips itself. Requiring the flag
at creation keeps the rail load-bearing.

---

## File Structure

| File | Responsibility |
|---|---|
| `skills/basket-manager/scripts/basket_weights.py` | Pure weight arithmetic: normalization, fill modes, weight validation. No I/O. |
| `skills/basket-manager/scripts/basket_events.py` | Event records, schema versioning, locked append, log reading. All file I/O for the log. |
| `skills/basket-manager/scripts/basket_store.py` | Replay events into basket state. Average-cost math. `order_id` dedupe. Snapshot export. |
| `skills/basket-manager/scripts/basket.py` | The command-line tool. Argument parsing, output formatting, exit codes. |
| `tests/test_basket_weights.py` | Weight arithmetic tests. |
| `tests/test_basket_events.py` | Log I/O, locking, and versioning tests. |
| `tests/test_basket_store.py` | Replay, money math, and dedupe tests. |
| `tests/test_basket_cli.py` | Subprocess tests for every subcommand. |

**Note on the spec:** §11.1 lists three new modules. This plan adds a fourth, `basket_weights.py`, because the weight arithmetic is substantial, is pure (no I/O), and is the part most worth testing in isolation. Everything else follows §11.1.

---

## Task 1: Weight normalization

**Files:**
- Create: `skills/basket-manager/scripts/basket_weights.py`
- Test: `tests/test_basket_weights.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `class WeightError(ValueError)`
  - `normalize_weights(weights: dict) -> dict` — maps symbol to a whole-number percent summing to 100.
  - `refill(others: dict, room: int, mode: str = "proportional") -> dict` — distributes `room` percent across `others`.
  - `MAX_HOLDINGS = 100`

- [ ] **Step 1: Write the failing test**

Create `tests/test_basket_weights.py`:

```python
#!/usr/bin/env python3
"""Unit tests for basket_weights.py."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "basket-manager" / "scripts"))

from basket_weights import MAX_HOLDINGS, WeightError, normalize_weights, refill


class TestNormalizeWeights(unittest.TestCase):

    def test_seven_equal_holdings(self):
        # 100/7 = 14.2857. Seven floors of 14 sum to 98, so two holdings gain
        # one percent. All fractional parts tie, so the alphabetical order
        # decides: AAPL and AMZN.
        symbols = ["NVDA", "MSFT", "AAPL", "GOOGL", "AMZN", "META", "SPCX"]
        result = normalize_weights({s: 1 for s in symbols})
        self.assertEqual(sum(result.values()), 100)
        self.assertEqual(result["AAPL"], 15)
        self.assertEqual(result["AMZN"], 15)
        for symbol in ["GOOGL", "META", "MSFT", "NVDA", "SPCX"]:
            self.assertEqual(result[symbol], 14)

    def test_ratio_input(self):
        result = normalize_weights({"NVDA": 2, "MSFT": 1})
        self.assertEqual(result, {"NVDA": 67, "MSFT": 33})

    def test_ratio_above_one_hundred(self):
        # An input carries no upper bound, because it can be a ratio.
        result = normalize_weights({"NVDA": 200, "MSFT": 100})
        self.assertEqual(result, {"NVDA": 67, "MSFT": 33})

    def test_three_equal_holdings(self):
        result = normalize_weights({"A": 1, "B": 1, "C": 1})
        self.assertEqual(sum(result.values()), 100)
        self.assertEqual(result, {"A": 34, "B": 33, "C": 33})

    def test_already_normal_set_is_unchanged(self):
        weights = {"NVDA": 50, "MSFT": 30, "AAPL": 20}
        self.assertEqual(normalize_weights(weights), weights)

    def test_normalization_is_repeatable(self):
        once = normalize_weights({s: 1 for s in ["AAPL", "AMZN", "GOOGL"]})
        twice = normalize_weights(once)
        self.assertEqual(once, twice)

    def test_decimal_input_is_allowed(self):
        result = normalize_weights({"NVDA": 14.29, "MSFT": 14.29})
        self.assertEqual(result, {"NVDA": 50, "MSFT": 50})

    def test_empty_set_gives_empty_result(self):
        self.assertEqual(normalize_weights({}), {})

    def test_zero_weight_is_refused(self):
        with self.assertRaises(WeightError) as ctx:
            normalize_weights({"NVDA": 0, "MSFT": 1})
        self.assertIn("NVDA", str(ctx.exception))

    def test_negative_weight_is_refused(self):
        with self.assertRaises(WeightError):
            normalize_weights({"NVDA": -5, "MSFT": 1})

    def test_non_numeric_weight_is_refused(self):
        with self.assertRaises(WeightError):
            normalize_weights({"NVDA": "many", "MSFT": 1})

    def test_boolean_weight_is_refused(self):
        # bool is a subclass of int; it must not pass as a weight.
        with self.assertRaises(WeightError):
            normalize_weights({"NVDA": True, "MSFT": 1})

    def test_too_many_holdings_is_refused(self):
        with self.assertRaises(WeightError) as ctx:
            normalize_weights({"S%d" % i: 1 for i in range(MAX_HOLDINGS + 1)})
        self.assertIn("100", str(ctx.exception))

    def test_result_that_would_round_to_zero_is_refused(self):
        # 1000:1 scales the second holding below half a percent.
        with self.assertRaises(WeightError) as ctx:
            normalize_weights({"NVDA": 1000, "MSFT": 1})
        self.assertIn("MSFT", str(ctx.exception))


class TestRefill(unittest.TestCase):

    def test_proportional_keeps_ratios(self):
        # NVDA drops to 20, leaving 80 for MSFT 30 and AAPL 20.
        result = refill({"MSFT": 30, "AAPL": 20}, 80, "proportional")
        self.assertEqual(result, {"MSFT": 48, "AAPL": 32})

    def test_equal_flattens(self):
        result = refill({"MSFT": 30, "AAPL": 20}, 80, "equal")
        self.assertEqual(result, {"MSFT": 40, "AAPL": 40})

    def test_proportional_is_the_default(self):
        self.assertEqual(
            refill({"MSFT": 30, "AAPL": 20}, 80),
            refill({"MSFT": 30, "AAPL": 20}, 80, "proportional"),
        )

    def test_result_sums_to_room(self):
        result = refill({"A": 1, "B": 1, "C": 1}, 70)
        self.assertEqual(sum(result.values()), 70)

    def test_room_below_holding_count_is_refused(self):
        # Two holdings cannot share one percent without one falling to 0.
        with self.assertRaises(WeightError):
            refill({"MSFT": 30, "AAPL": 20}, 1)

    def test_a_skewed_set_that_would_round_to_zero_is_refused(self):
        # room >= len(others) is not enough on its own. 49:1 over 2 percent
        # gives 1.96 and 0.04, and the shortfall goes to the larger holding.
        with self.assertRaises(WeightError) as ctx:
            refill({"MSFT": 49, "AAPL": 1}, 2)
        self.assertIn("AAPL", str(ctx.exception))

    def test_unknown_mode_is_refused(self):
        with self.assertRaises(WeightError):
            refill({"MSFT": 30}, 80, "sideways")

    def test_empty_others_gives_empty_result(self):
        self.assertEqual(refill({}, 0), {})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.test_basket_weights -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'basket_weights'`

- [ ] **Step 3: Write the implementation**

Create `skills/basket-manager/scripts/basket_weights.py`:

```python
#!/usr/bin/env python3
"""Whole-number target weights for a basket.

A basket's target weights are whole numbers of percent that always sum to
exactly 100. This module owns that arithmetic. It performs no input or output.
"""

MAX_HOLDINGS = 100
TOTAL_PERCENT = 100

FILL_PROPORTIONAL = "proportional"
FILL_EQUAL = "equal"
FILL_MODES = (FILL_PROPORTIONAL, FILL_EQUAL)


class WeightError(ValueError):
    """A weight set breaks a rule from section 6.7 of the design."""


def _check_input(weights):
    """Reject a weight that is not a positive number."""
    for symbol, value in weights.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise WeightError(
                "The weight for %s is not a number: %r" % (symbol, value)
            )
        if value <= 0:
            raise WeightError(
                "The weight for %s must be above 0, but it is %s" % (symbol, value)
            )
    if len(weights) > MAX_HOLDINGS:
        raise WeightError(
            "A basket holds at most %d holdings, but this set holds %d"
            % (MAX_HOLDINGS, len(weights))
        )


def _largest_remainder(exact, target):
    """Round exact shares to whole numbers that sum to target.

    Each holding takes the whole part of its exact share. The shortfall then
    goes one percent at a time to the holdings with the largest fractional
    part. A tie breaks by symbol, in alphabetical order.
    """
    result = {}
    for symbol, value in exact.items():
        result[symbol] = int(value)

    shortfall = target - sum(result.values())
    ranked = sorted(exact, key=lambda s: (-(exact[s] - int(exact[s])), s))
    for symbol in ranked[:shortfall]:
        result[symbol] += 1
    return result


def normalize_weights(weights):
    """Scale a weight set to whole numbers that sum to exactly 100.

    The input may hold decimals, and it may be a ratio such as {'NVDA': 2,
    'MSFT': 1}. The output holds whole numbers between 1 and 100.

    Raises WeightError when an input is not a positive number, when the set
    holds more than MAX_HOLDINGS symbols, or when a result would fall to 0.
    """
    if not weights:
        return {}

    _check_input(weights)

    total = float(sum(weights.values()))
    exact = {}
    for symbol, value in weights.items():
        exact[symbol] = value * TOTAL_PERCENT / total

    result = _largest_remainder(exact, TOTAL_PERCENT)

    zeros = sorted(s for s, v in result.items() if v < 1)
    if zeros:
        raise WeightError(
            "These holdings would fall to 0 percent: %s. "
            "Remove them, or give them a larger share." % ", ".join(zeros)
        )
    return result


def refill(others, room, mode=FILL_PROPORTIONAL):
    """Distribute `room` percent across `others`.

    FILL_PROPORTIONAL keeps the ratios between the other holdings.
    FILL_EQUAL gives every other holding the same weight.

    Raises WeightError when the mode is unknown, or when `room` is too small
    to give every holding at least one percent.
    """
    if mode not in FILL_MODES:
        raise WeightError(
            "The fill mode %r is unknown. Use one of: %s"
            % (mode, ", ".join(FILL_MODES))
        )
    if not others:
        return {}
    if room < len(others):
        raise WeightError(
            "%d percent cannot cover %d holdings, because every holding needs "
            "at least 1 percent. Remove a holding instead." % (room, len(others))
        )

    if mode == FILL_EQUAL:
        source = {}
        for symbol in others:
            source[symbol] = 1.0
    else:
        source = dict(others)

    total = float(sum(source.values()))
    exact = {}
    for symbol, value in source.items():
        exact[symbol] = value * room / total

    result = _largest_remainder(exact, room)

    # `room >= len(others)` is not enough. A skewed set can still round a
    # small holding to 0: {MSFT: 49, AAPL: 1} with room 2 gives 1.96 and 0.04,
    # and the shortfall goes to MSFT.
    zeros = sorted(s for s, v in result.items() if v < 1)
    if zeros:
        raise WeightError(
            "These holdings would fall to 0 percent: %s. Remove them, or give "
            "the named holding a smaller weight." % ", ".join(zeros))
    return result
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m unittest tests.test_basket_weights -v`
Expected: PASS, 22 tests.

- [ ] **Step 5: Run the whole suite to check for regressions**

Run: `python3 -m unittest discover -s tests`
Expected: OK, 65 tests (43 existing plus 22 new).

- [ ] **Step 6: Commit**

```bash
git add skills/basket-manager/scripts/basket_weights.py tests/test_basket_weights.py
git commit -m "feat(basket): add whole-number weight normalization

Largest remainder method with an alphabetical tiebreak. Weights always
sum to exactly 100. Refuses a result that would fall to 0 percent."
```

---

## Task 2: The event log

**Files:**
- Create: `skills/basket-manager/scripts/basket_events.py`
- Test: `tests/test_basket_events.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `EVENT_VERSION = 1`
  - `class EventLogError(Exception)`
  - `class CorruptLineError(EventLogError)` with attributes `line_number` and `raw`
  - `class EventLog` with:
    - `EventLog(data_dir: Path)`
    - `.path` — the log file path
    - `.read() -> list` — every event as a dict, in log order, each with `_line` set to its 1-based line number
    - `.append(events: list) -> None` — writes each event as one line, then flushes and fsyncs
    - `.locked()` — a context manager holding an exclusive `flock` on the log
    - `.count() -> int` — number of lines
  - `make_event(event_type: str, slug: str, **fields) -> dict` — adds `v`, `ts`, `type`, `slug`

- [ ] **Step 1: Write the failing test**

Create `tests/test_basket_events.py`:

```python
#!/usr/bin/env python3
"""Unit tests for basket_events.py."""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "basket-manager" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from basket_events import (
    EVENT_VERSION,
    CorruptLineError,
    EventLog,
    make_event,
)


class TestMakeEvent(unittest.TestCase):

    def test_adds_the_required_fields(self):
        event = make_event("basket_created", "mag7", name="Magnificent 7")
        self.assertEqual(event["v"], EVENT_VERSION)
        self.assertEqual(event["type"], "basket_created")
        self.assertEqual(event["slug"], "mag7")
        self.assertEqual(event["name"], "Magnificent 7")
        self.assertTrue(event["ts"].endswith("Z"))

    def test_accepts_an_explicit_timestamp(self):
        event = make_event("buy", "mag7", ts="2026-07-23T19:25:23Z", shares=1.0)
        self.assertEqual(event["ts"], "2026-07-23T19:25:23Z")


class TestEventLog(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = EventLog(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_read_of_a_missing_log_gives_no_events(self):
        self.assertEqual(self.log.read(), [])
        self.assertEqual(self.log.count(), 0)

    def test_append_then_read_round_trip(self):
        self.log.append([make_event("basket_created", "mag7", name="M7")])
        events = self.log.read()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["slug"], "mag7")
        self.assertEqual(events[0]["_line"], 1)

    def test_append_adds_to_the_end(self):
        self.log.append([make_event("basket_created", "a", name="A")])
        self.log.append([make_event("basket_created", "b", name="B")])
        slugs = [e["slug"] for e in self.log.read()]
        self.assertEqual(slugs, ["a", "b"])

    def test_append_writes_one_line_for_each_event(self):
        self.log.append([
            make_event("holding_added", "a", symbol="NVDA", weight=50),
            make_event("holding_added", "a", symbol="MSFT", weight=50),
        ])
        self.assertEqual(self.log.count(), 2)

    def test_each_line_is_valid_json_on_its_own(self):
        self.log.append([make_event("basket_created", "a", name="A")])
        raw = self.log.path.read_text().splitlines()
        self.assertEqual(len(raw), 1)
        json.loads(raw[0])

    def test_a_corrupt_line_raises_with_its_number(self):
        self.log.append([make_event("basket_created", "a", name="A")])
        with self.log.path.open("a") as handle:
            handle.write("{not json\n")
        with self.assertRaises(CorruptLineError) as ctx:
            self.log.read()
        self.assertEqual(ctx.exception.line_number, 2)

    def test_a_blank_line_is_skipped(self):
        self.log.append([make_event("basket_created", "a", name="A")])
        with self.log.path.open("a") as handle:
            handle.write("\n")
        self.assertEqual(len(self.log.read()), 1)

    def test_locked_is_reentrant_for_one_process(self):
        with self.log.locked():
            self.log.append([make_event("basket_created", "a", name="A")])
        self.assertEqual(self.log.count(), 1)


class TestConcurrentAppend(unittest.TestCase):
    """Two processes must not interleave a validate-then-append sequence."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_two_processes_do_not_both_append_the_same_id(self):
        # Each process holds the lock, reads the log, and appends only when
        # the id is absent. Exactly one append must win.
        program = textwrap.dedent(
            """
            import sys, time
            sys.path.insert(0, %r)
            from pathlib import Path
            from basket_events import EventLog, make_event

            log = EventLog(Path(sys.argv[1]))
            with log.locked():
                seen = any(e.get("order_id") == "X" for e in log.read())
                time.sleep(0.2)          # widen the race window
                if not seen:
                    log.append([make_event("buy", "a", order_id="X")])
            """
            % str(SCRIPTS)
        )
        script = self.dir / "worker.py"
        script.write_text(program)

        procs = [
            subprocess.Popen([sys.executable, str(script), str(self.dir)])
            for _ in range(2)
        ]
        for proc in procs:
            proc.wait(timeout=30)

        log = EventLog(self.dir)
        appended = [e for e in log.read() if e.get("order_id") == "X"]
        self.assertEqual(len(appended), 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.test_basket_events -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'basket_events'`

- [ ] **Step 3: Write the implementation**

Create `skills/basket-manager/scripts/basket_events.py`:

```python
#!/usr/bin/env python3
"""The append-only event log.

The log is the source of truth for every basket. This module owns all input
and output for that file, and it owns the lock that makes a write safe.
"""

import datetime
import fcntl
import json
import os
from contextlib import contextmanager

EVENT_VERSION = 1
LOG_NAME = "events.log.jsonl"


class EventLogError(Exception):
    """The log cannot be read or written."""


class CorruptLineError(EventLogError):
    """A line in the log is not valid JSON."""

    def __init__(self, line_number, raw):
        self.line_number = line_number
        self.raw = raw
        super(CorruptLineError, self).__init__(
            "Line %d of the event log is not valid JSON: %r"
            % (line_number, raw[:80])
        )


def utc_now():
    """Return the current time as an ISO 8601 string in UTC."""
    now = datetime.datetime.now(datetime.timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def make_event(event_type, slug, **fields):
    """Build an event with the fields that every event carries."""
    event = {
        "v": EVENT_VERSION,
        "ts": fields.pop("ts", None) or utc_now(),
        "type": event_type,
        "slug": slug,
    }
    event.update(fields)
    return event


class EventLog(object):
    """Read and append the event log under a lock."""

    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.path = data_dir / LOG_NAME
        self._depth = 0
        self._handle = None

    def _ensure_parent(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def locked(self):
        """Hold an exclusive lock on the log for the whole block.

        The lock covers validation and append together. Without it, two
        sessions can both read, both find no duplicate, and both write.
        """
        self._ensure_parent()
        if self._depth == 0:
            self._handle = self.path.open("a+")
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX)
        self._depth += 1
        try:
            yield
        finally:
            self._depth -= 1
            if self._depth == 0:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
                self._handle.close()
                self._handle = None

    def _parse(self, handle):
        events = []
        for number, raw in enumerate(handle, start=1):
            text = raw.strip()
            if not text:
                continue
            try:
                event = json.loads(text)
            except ValueError:
                raise CorruptLineError(number, text)
            event["_line"] = number
            events.append(event)
        return events

    def read(self):
        """Return every event in log order.

        Each event carries a `_line` key that holds its 1-based line number.
        Raises CorruptLineError on the first line that is not valid JSON.

        A read takes a shared lock, so it never sees a half-written last line
        while another session appends. When this object already holds the
        exclusive lock, it must not ask for a second lock: flock ties a lock
        to the open file description, so a new descriptor in the same process
        would block against our own exclusive lock and deadlock.
        """
        if not self.path.exists():
            return []

        if self._depth > 0:
            with self.path.open("r") as handle:
                return self._parse(handle)

        with self.path.open("r") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            try:
                return self._parse(handle)
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def count(self):
        """Return the number of events in the log."""
        if not self.path.exists():
            return 0
        total = 0
        with self.path.open("r") as handle:
            for raw in handle:
                if raw.strip():
                    total += 1
        return total

    def append(self, events):
        """Append events to the log and make them durable.

        The caller normally holds the lock already. This method takes it when
        the caller does not, so a bare append stays safe.
        """
        if not events:
            return
        with self.locked():
            for event in events:
                payload = {k: v for k, v in event.items() if k != "_line"}
                line = json.dumps(payload, separators=(",", ":"), sort_keys=False)
                self._handle.write(line + "\n")
            self._handle.flush()
            os.fsync(self._handle.fileno())
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m unittest tests.test_basket_events -v`
Expected: PASS, 11 tests. The concurrency test takes about half a second.

- [ ] **Step 5: Run the whole suite**

Run: `python3 -m unittest discover -s tests`
Expected: OK, 76 tests.

- [ ] **Step 6: Commit**

```bash
git add skills/basket-manager/scripts/basket_events.py tests/test_basket_events.py
git commit -m "feat(basket): add the append-only event log

Locked append with fsync, line-numbered reads, and a corrupt-line error
that names the line. A concurrency test proves two processes cannot both
append the same order id."
```

---

## Task 3: Replay and basket state

**Files:**
- Create: `skills/basket-manager/scripts/basket_store.py`
- Test: `tests/test_basket_store.py`

**Interfaces:**
- Consumes: `basket_events.EventLog`, `basket_events.CorruptLineError`.
- Produces:
  - `class StoreError(Exception)`
  - `class Position` with attributes `shares`, `avg_cost`, `realized_pnl`, and property `total_invested`
  - `class Holding` with attributes `symbol`, `target_weight_pct`, `thesis`, `position`
  - `class Basket` with attributes `slug`, `name`, `description`, `account_number`, `rebalance_threshold_pct`, `created_at`, `updated_at`, `holdings` (an ordered dict of symbol to `Holding`), `total_invested`, `realized_pnl`
  - `class ReplayResult` with attributes `baskets` (dict of slug to `Basket`), `ignored` (list of dicts with `line`, `order_id`, `slug`, `kept_by`), `order_index` (dict of `order_id` to slug)
  - `replay(events: list) -> ReplayResult`
  - `slugify(name: str) -> str`
  - `snapshot_dict(basket: Basket) -> dict`

- [ ] **Step 1: Write the failing test**

Create `tests/test_basket_store.py`:

```python
#!/usr/bin/env python3
"""Unit tests for basket_store.py."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "basket-manager" / "scripts"))

from basket_events import make_event
from basket_store import replay, slugify, snapshot_dict


def created(slug="mag7", name="Magnificent 7", **kw):
    return make_event("basket_created", slug, name=name,
                      description=kw.get("description", ""),
                      account_number=kw.get("account_number", "000000000"),
                      rebalance_threshold_pct=kw.get("threshold", 5.0),
                      ts=kw.get("ts", "2026-07-23T12:22:00Z"))


def holding(slug="mag7", symbol="NVDA", weight=50, thesis=""):
    return make_event("holding_added", slug, symbol=symbol, weight=weight,
                      thesis=thesis, ts="2026-07-23T12:22:01Z")


def buy(slug="mag7", symbol="NVDA", shares=10.0, price=50.0, order_id="o1",
        ts="2026-07-24T00:00:00Z", amount=None):
    return make_event("buy", slug, symbol=symbol, shares=shares, price=price,
                      amount=amount if amount is not None else shares * price,
                      order_id=order_id, ts=ts)


def sell(slug="mag7", symbol="NVDA", shares=5.0, price=70.0, order_id="o2",
         ts="2026-07-25T00:00:00Z"):
    return make_event("sell", slug, symbol=symbol, shares=shares, price=price,
                      amount=shares * price, order_id=order_id, ts=ts)


class TestSlugify(unittest.TestCase):

    def test_lowercases_and_hyphenates(self):
        self.assertEqual(slugify("Magnificent 7 Index"), "magnificent-7-index")

    def test_strips_punctuation(self):
        self.assertEqual(slugify("Storage & Memory"), "storage-memory")

    def test_collapses_repeated_separators(self):
        self.assertEqual(slugify("A   --  B"), "a-b")


class TestReplayDefinition(unittest.TestCase):

    def test_empty_log_gives_no_baskets(self):
        self.assertEqual(replay([]).baskets, {})

    def test_created_then_holdings(self):
        result = replay([created(), holding(symbol="NVDA", weight=60),
                         holding(symbol="MSFT", weight=40)])
        basket = result.baskets["mag7"]
        self.assertEqual(basket.name, "Magnificent 7")
        self.assertEqual(list(basket.holdings), ["NVDA", "MSFT"])
        self.assertEqual(basket.holdings["NVDA"].target_weight_pct, 60)

    def test_weight_changed(self):
        result = replay([created(), holding(symbol="NVDA", weight=60),
                         make_event("weight_changed", "mag7", symbol="NVDA",
                                    **{"from": 60, "to": 70})])
        self.assertEqual(result.baskets["mag7"].holdings["NVDA"].target_weight_pct, 70)

    def test_thesis_changed(self):
        result = replay([created(), holding(symbol="NVDA", thesis="old"),
                         make_event("thesis_changed", "mag7", symbol="NVDA",
                                    thesis="new")])
        self.assertEqual(result.baskets["mag7"].holdings["NVDA"].thesis, "new")

    def test_basket_updated(self):
        result = replay([created(),
                         make_event("basket_updated", "mag7",
                                    field="rebalance_threshold_pct",
                                    **{"from": 5.0, "to": 3.0})])
        self.assertEqual(result.baskets["mag7"].rebalance_threshold_pct, 3.0)

    def test_holding_removed(self):
        result = replay([created(), holding(symbol="NVDA"), holding(symbol="MSFT"),
                         make_event("holding_removed", "mag7", symbol="NVDA")])
        self.assertEqual(list(result.baskets["mag7"].holdings), ["MSFT"])

    def test_basket_deleted_removes_it_from_the_result(self):
        result = replay([created(), holding(), make_event("basket_deleted", "mag7")])
        self.assertNotIn("mag7", result.baskets)

    def test_a_slug_can_start_a_second_lifetime(self):
        events = [created(name="First"), holding(symbol="NVDA"),
                  make_event("basket_deleted", "mag7"),
                  created(name="Second", ts="2026-08-01T00:00:00Z")]
        basket = replay(events).baskets["mag7"]
        self.assertEqual(basket.name, "Second")
        self.assertEqual(list(basket.holdings), [])

    def test_an_event_after_deletion_without_recreation_is_ignored(self):
        events = [created(), make_event("basket_deleted", "mag7"),
                  holding(symbol="NVDA")]
        self.assertEqual(replay(events).baskets, {})

    def test_an_older_event_version_is_accepted(self):
        # The log keeps every line forever, so a reader must accept an old
        # shape. Section 9.5 of the design gives the rule.
        old_created = created()
        old_created["v"] = 0
        old_holding = holding(symbol="NVDA", weight=100)
        old_holding["v"] = 0
        basket = replay([old_created, old_holding]).baskets["mag7"]
        self.assertEqual(basket.holdings["NVDA"].target_weight_pct, 100)

    def test_an_unknown_event_type_is_ignored(self):
        # A future version can add an event type. An older reader must skip it
        # rather than fail.
        events = [created(), holding(symbol="NVDA", weight=100),
                  make_event("something_new", "mag7", detail="x")]
        basket = replay(events).baskets["mag7"]
        self.assertEqual(list(basket.holdings), ["NVDA"])

    def test_a_missing_optional_field_uses_a_default(self):
        # A new field must have a default, so an old line stays valid.
        bare = make_event("basket_created", "bare", name="Bare")
        basket = replay([bare]).baskets["bare"]
        self.assertEqual(basket.rebalance_threshold_pct, 5.0)
        self.assertEqual(basket.description, "")


class TestReplayMoney(unittest.TestCase):

    def test_a_single_buy(self):
        result = replay([created(), holding(), buy(shares=10.0, price=50.0)])
        position = result.baskets["mag7"].holdings["NVDA"].position
        self.assertEqual(position.shares, 10.0)
        self.assertEqual(position.avg_cost, 50.0)
        self.assertEqual(position.total_invested, 500.0)

    def test_average_cost_across_two_buys(self):
        result = replay([created(), holding(),
                         buy(shares=10.0, price=50.0, order_id="o1"),
                         buy(shares=10.0, price=60.0, order_id="o2")])
        position = result.baskets["mag7"].holdings["NVDA"].position
        self.assertEqual(position.shares, 20.0)
        self.assertEqual(position.avg_cost, 55.0)
        self.assertEqual(position.total_invested, 1100.0)

    def test_a_partial_sell(self):
        result = replay([created(), holding(),
                         buy(shares=10.0, price=50.0, order_id="o1"),
                         sell(shares=4.0, price=70.0, order_id="o2")])
        position = result.baskets["mag7"].holdings["NVDA"].position
        self.assertEqual(position.shares, 6.0)
        self.assertEqual(position.avg_cost, 50.0)
        self.assertEqual(position.realized_pnl, 80.0)

    def test_a_sell_of_all_shares(self):
        result = replay([created(), holding(),
                         buy(shares=10.0, price=50.0, order_id="o1"),
                         sell(shares=10.0, price=70.0, order_id="o2")])
        position = result.baskets["mag7"].holdings["NVDA"].position
        self.assertEqual(position.shares, 0.0)
        self.assertEqual(position.avg_cost, 0.0)
        self.assertEqual(position.realized_pnl, 200.0)

    def test_the_replay_uses_shares_times_price_and_ignores_amount(self):
        # A dollar order requests 10.00 and fills 0.048067 at 208.04, which is
        # 10.0006. Exactly one field can be authoritative.
        result = replay([created(), holding(),
                         buy(shares=0.048067, price=208.04, amount=10.00)])
        position = result.baskets["mag7"].holdings["NVDA"].position
        self.assertAlmostEqual(position.total_invested, 0.048067 * 208.04, places=6)

    def test_basket_totals_sum_the_holdings(self):
        events = [created(), holding(symbol="NVDA"), holding(symbol="MSFT"),
                  buy(symbol="NVDA", shares=10.0, price=50.0, order_id="o1"),
                  buy(symbol="MSFT", shares=2.0, price=100.0, order_id="o2")]
        basket = replay(events).baskets["mag7"]
        self.assertEqual(basket.total_invested, 700.0)

    def test_a_trade_for_an_unknown_symbol_is_ignored(self):
        result = replay([created(), holding(symbol="NVDA"),
                         buy(symbol="TSLA", order_id="o9")])
        self.assertNotIn("TSLA", result.baskets["mag7"].holdings)


class TestReplayDedupe(unittest.TestCase):

    def test_a_repeated_order_id_in_the_same_basket_is_ignored(self):
        result = replay([created(), holding(),
                         buy(shares=10.0, price=50.0, order_id="dup"),
                         buy(shares=10.0, price=50.0, order_id="dup")])
        position = result.baskets["mag7"].holdings["NVDA"].position
        self.assertEqual(position.shares, 10.0)
        self.assertEqual(len(result.ignored), 1)

    def test_an_ignored_event_is_reported_with_its_line_and_baskets(self):
        events = [created(slug="a", name="A"), holding(slug="a"),
                  created(slug="b", name="B"), holding(slug="b"),
                  buy(slug="a", order_id="dup"),
                  buy(slug="b", order_id="dup")]
        for number, event in enumerate(events, start=1):
            event["_line"] = number
        result = replay(events)
        self.assertEqual(len(result.ignored), 1)
        report = result.ignored[0]
        self.assertEqual(report["order_id"], "dup")
        self.assertEqual(report["slug"], "b")
        self.assertEqual(report["kept_by"], "a")
        self.assertEqual(report["line"], 6)

    def test_the_first_event_wins(self):
        result = replay([created(), holding(),
                         buy(shares=10.0, price=50.0, order_id="dup"),
                         buy(shares=99.0, price=1.0, order_id="dup")])
        self.assertEqual(result.baskets["mag7"].holdings["NVDA"].position.shares, 10.0)

    def test_the_order_index_maps_ids_to_slugs(self):
        result = replay([created(), holding(), buy(order_id="o1")])
        self.assertEqual(result.order_index["o1"], "mag7")


class TestSnapshotDict(unittest.TestCase):

    def test_shape_matches_the_spec(self):
        events = [created(), holding(symbol="NVDA", weight=100, thesis="GPU"),
                  buy(shares=10.0, price=50.0)]
        data = snapshot_dict(replay(events).baskets["mag7"])
        self.assertEqual(data["slug"], "mag7")
        self.assertEqual(data["schema_version"], 2)
        self.assertEqual(len(data["holdings"]), 1)
        self.assertEqual(data["holdings"][0]["symbol"], "NVDA")
        self.assertEqual(data["holdings"][0]["position"]["shares"], 10.0)
        self.assertEqual(data["totals"]["total_invested"], 500.0)
        self.assertIn("built_at", data["totals"])

    def test_a_holding_without_a_position_is_null(self):
        data = snapshot_dict(replay([created(), holding()]).baskets["mag7"])
        self.assertIsNone(data["holdings"][0]["position"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.test_basket_store -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'basket_store'`

- [ ] **Step 3: Write the implementation**

Create `skills/basket-manager/scripts/basket_store.py`:

```python
#!/usr/bin/env python3
"""Replay the event log into basket state.

Every read replays the log. The store holds no cache. This module owns the
average-cost arithmetic and the order-id dedupe rule.
"""

import re
from collections import OrderedDict

from basket_events import utc_now

SCHEMA_VERSION = 2
TRADE_TYPES = ("buy", "sell")


class StoreError(Exception):
    """The log cannot be replayed into a valid state."""


def slugify(name):
    """Build a slug from a basket name."""
    text = name.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


class Position(object):
    """The shares of one symbol that one basket holds."""

    def __init__(self, shares=0.0, avg_cost=0.0, realized_pnl=0.0):
        self.shares = shares
        self.avg_cost = avg_cost
        self.realized_pnl = realized_pnl

    @property
    def total_invested(self):
        return self.shares * self.avg_cost

    def buy(self, shares, price):
        """Apply a purchase with the average cost method."""
        total_cost = self.total_invested + (shares * price)
        self.shares = self.shares + shares
        self.avg_cost = total_cost / self.shares if self.shares > 0 else 0.0

    def sell(self, shares, price):
        """Apply a sale with the average cost method.

        The average cost does not change. The gain or loss goes to
        realized_pnl. A sale of every share leaves the average cost at zero.

        Returns the share count that it could not cover. A caller that gets a
        non-zero result must report it; a silent clamp would credit a profit
        on shares the basket never held.
        """
        oversold = 0.0
        if shares > self.shares + 1e-12:
            oversold = shares - self.shares
            shares = self.shares

        self.realized_pnl += (price - self.avg_cost) * shares
        self.shares = self.shares - shares
        if self.shares <= 1e-12:
            self.shares = 0.0
            self.avg_cost = 0.0
        return oversold


class Holding(object):
    """One symbol in a basket: its target weight and its position."""

    def __init__(self, symbol, target_weight_pct=0, thesis=""):
        self.symbol = symbol
        self.target_weight_pct = target_weight_pct
        self.thesis = thesis
        self.position = Position()

    @property
    def has_position(self):
        return self.position.shares > 0


class Basket(object):
    """One basket, rebuilt from its events."""

    def __init__(self, slug):
        self.slug = slug
        self.name = slug
        self.description = ""
        self.account_number = ""
        self.rebalance_threshold_pct = 5.0
        self.created_at = ""
        self.updated_at = ""
        self.holdings = OrderedDict()

    @property
    def target_weights(self):
        return dict((s, h.target_weight_pct) for s, h in self.holdings.items())

    @property
    def total_invested(self):
        return sum(h.position.total_invested for h in self.holdings.values())

    @property
    def realized_pnl(self):
        return sum(h.position.realized_pnl for h in self.holdings.values())


class ReplayResult(object):
    """The outcome of a replay."""

    def __init__(self, baskets, ignored, order_index, clamped=None):
        self.baskets = baskets
        self.ignored = ignored
        self.order_index = order_index
        self.clamped = clamped if clamped is not None else []


def replay(events):
    """Build every live basket from the events, in log order.

    A `basket_deleted` event ends a lifetime. A later `basket_created` event
    with the same slug starts a new one, and the old events stay with the old
    basket.

    A `buy` or `sell` event whose `order_id` already appeared is ignored. The
    first event wins. Every ignored event goes into the result.
    """
    baskets = OrderedDict()
    ignored = []
    order_index = {}
    clamped = []

    for event in events:
        slug = event.get("slug")
        event_type = event.get("type")
        if not slug or not event_type:
            continue

        if event_type == "basket_created":
            basket = Basket(slug)
            basket.name = event.get("name", slug)
            basket.description = event.get("description", "")
            basket.account_number = event.get("account_number", "")
            basket.rebalance_threshold_pct = event.get(
                "rebalance_threshold_pct", 5.0)
            basket.created_at = event.get("ts", "")
            basket.updated_at = event.get("ts", "")
            baskets[slug] = basket
            continue

        basket = baskets.get(slug)
        if basket is None:
            # The basket does not exist in this lifetime. The event belongs to
            # a deleted basket, so the live state ignores it.
            continue
        basket.updated_at = event.get("ts", basket.updated_at)

        if event_type == "basket_deleted":
            del baskets[slug]

        elif event_type == "basket_updated":
            field = event.get("field")
            if field in ("name", "description", "rebalance_threshold_pct"):
                setattr(basket, field, event.get("to"))

        elif event_type == "holding_added":
            symbol = event.get("symbol")
            if symbol and symbol not in basket.holdings:
                basket.holdings[symbol] = Holding(
                    symbol, event.get("weight", 0), event.get("thesis", ""))

        elif event_type == "holding_removed":
            basket.holdings.pop(event.get("symbol"), None)

        elif event_type == "weight_changed":
            holding = basket.holdings.get(event.get("symbol"))
            if holding is not None:
                holding.target_weight_pct = event.get("to")

        elif event_type == "thesis_changed":
            holding = basket.holdings.get(event.get("symbol"))
            if holding is not None:
                holding.thesis = event.get("thesis", "")

        elif event_type in TRADE_TYPES:
            order_id = event.get("order_id")
            if order_id and order_id in order_index:
                ignored.append({
                    "line": event.get("_line"),
                    "order_id": order_id,
                    "slug": slug,
                    "kept_by": order_index[order_id],
                })
                continue

            holding = basket.holdings.get(event.get("symbol"))
            if holding is None:
                continue

            shares = float(event.get("shares", 0.0))
            price = float(event.get("price", 0.0))
            if event_type == "buy":
                holding.position.buy(shares, price)
            else:
                oversold = holding.position.sell(shares, price)
                if oversold > 0:
                    clamped.append({
                        "line": event.get("_line"),
                        "order_id": order_id,
                        "slug": slug,
                        "symbol": event.get("symbol"),
                        "requested": shares,
                        "oversold": oversold,
                    })

            if order_id:
                order_index[order_id] = slug

    return ReplayResult(baskets, ignored, order_index, clamped)


def snapshot_dict(basket):
    """Build the export file contents for one basket."""
    holdings = []
    for holding in basket.holdings.values():
        position = None
        if holding.has_position:
            position = {
                "shares": round(holding.position.shares, 6),
                "avg_cost": round(holding.position.avg_cost, 4),
                "total_invested": round(holding.position.total_invested, 2),
                "realized_pnl": round(holding.position.realized_pnl, 2),
            }
        holdings.append({
            "symbol": holding.symbol,
            "target_weight_pct": holding.target_weight_pct,
            "thesis": holding.thesis,
            "position": position,
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "name": basket.name,
        "slug": basket.slug,
        "description": basket.description,
        "account_number": basket.account_number,
        "rebalance_threshold_pct": basket.rebalance_threshold_pct,
        "created_at": basket.created_at,
        "updated_at": basket.updated_at,
        "holdings": holdings,
        "totals": {
            "total_invested": round(basket.total_invested, 2),
            "realized_pnl": round(basket.realized_pnl, 2),
            "built_at": utc_now(),
        },
    }
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m unittest tests.test_basket_store -v`
Expected: PASS, 28 tests.

- [ ] **Step 5: Run the whole suite**

Run: `python3 -m unittest discover -s tests`
Expected: OK, 104 tests.

- [ ] **Step 6: Commit**

```bash
git add skills/basket-manager/scripts/basket_store.py tests/test_basket_store.py
git commit -m "feat(basket): replay events into basket state

Average-cost math, order-id dedupe with a report of every ignored event,
and slug lifetimes across delete and recreate. The replay uses shares
times price and ignores the amount field."
```

---

## Task 4: CLI skeleton with create, list, and show

**Files:**
- Create: `skills/basket-manager/scripts/basket.py`
- Test: `tests/test_basket_cli.py`

**Interfaces:**
- Consumes: `basket_weights.normalize_weights`, `basket_events.EventLog`, `basket_events.make_event`, `basket_store.replay`, `basket_store.slugify`, `basket_store.snapshot_dict`.
- Produces:
  - `main(argv: list) -> int` — the entry point that returns an exit code.
  - `class CliError(Exception)` with attributes `code` (a string such as `"BASKET_NOT_FOUND"`), `detail` (a dict), and `exit_code` (an int, default 1).
  - `DEFAULT_DATA_DIR = Path.home() / ".tradethos"`
  - Every subcommand accepts `--data-dir` (hidden from help) and `--format {json,table}`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_basket_cli.py`:

```python
#!/usr/bin/env python3
"""Subprocess tests for the basket command-line tool.

These tests run the tool as a real process. The defect that the v0.2.2 review
found lived in main(), and unit tests alone did not reach it.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "skills" / "basket-manager" / "scripts" / "basket.py"


class CliTestCase(unittest.TestCase):
    """Base class that gives each test its own data directory."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        """Run the tool and return (exit_code, stdout_object, stderr_text)."""
        proc = subprocess.run(
            [sys.executable, str(CLI), "--data-dir", str(self.data_dir)] + list(args),
            capture_output=True, text=True,
        )
        payload = None
        if proc.stdout.strip():
            try:
                payload = json.loads(proc.stdout)
            except ValueError:
                payload = proc.stdout
        return proc.returncode, payload, proc.stderr

    def make_basket(self, name="Magnificent 7", symbols="NVDA:1,MSFT:1"):
        code, out, err = self.run_cli("create", name, "--symbols", symbols,
                                      "--account", "000000000")
        self.assertEqual(code, 0, err)
        return out["slug"]


class TestCreate(CliTestCase):

    def test_create_returns_the_normalized_weights(self):
        code, out, err = self.run_cli(
            "create", "Magnificent 7", "--symbols",
            "NVDA:1,MSFT:1,AAPL:1,GOOGL:1,AMZN:1,META:1,SPCX:1",
            "--account", "000000000")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["slug"], "magnificent-7")
        weights = dict((h["symbol"], h["target_weight_pct"]) for h in out["holdings"])
        self.assertEqual(sum(weights.values()), 100)
        self.assertEqual(weights["AAPL"], 15)
        self.assertEqual(weights["AMZN"], 15)
        self.assertEqual(weights["NVDA"], 14)

    def test_create_reports_the_weights_it_changed(self):
        code, out, _ = self.run_cli("create", "Trio", "--symbols", "A:1,B:1,C:1",
                                    "--account", "000000000")
        self.assertEqual(code, 0)
        self.assertTrue(out["normalized"])

    def test_create_writes_no_weight_changed_event(self):
        self.make_basket()
        code, out, _ = self.run_cli("history", "--format", "json")
        self.assertEqual(code, 0)
        types = [e["type"] for e in out["events"]]
        self.assertNotIn("weight_changed", types)

    def test_a_duplicate_slug_is_refused(self):
        self.make_basket()
        code, _, err = self.run_cli("create", "Magnificent 7", "--symbols",
                                    "NVDA:1", "--account", "000000000")
        self.assertEqual(code, 1)
        self.assertIn("SLUG_EXISTS", err)

    def test_a_zero_weight_is_refused(self):
        code, _, err = self.run_cli("create", "Bad", "--symbols", "NVDA:0,MSFT:1",
                                    "--account", "000000000")
        self.assertEqual(code, 1)
        self.assertIn("NVDA", err)

    def test_symbols_become_upper_case(self):
        code, out, _ = self.run_cli("create", "Lower", "--symbols", "nvda:1",
                                    "--account", "000000000")
        self.assertEqual(code, 0)
        self.assertEqual(out["holdings"][0]["symbol"], "NVDA")


class TestList(CliTestCase):

    def test_list_of_an_empty_store(self):
        code, out, err = self.run_cli("list")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["baskets"], [])

    def test_list_shows_each_basket(self):
        self.make_basket(name="One", symbols="A:1")
        self.make_basket(name="Two", symbols="B:1")
        code, out, _ = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertEqual(sorted(b["slug"] for b in out["baskets"]), ["one", "two"])

    def test_table_format_prints_text(self):
        self.make_basket(name="One", symbols="A:1")
        proc = subprocess.run(
            [sys.executable, str(CLI), "--data-dir", str(self.data_dir),
             "list", "--format", "table"],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("One", proc.stdout)


class TestShow(CliTestCase):

    def test_show_reports_targets_and_no_position(self):
        slug = self.make_basket()
        code, out, err = self.run_cli("show", slug)
        self.assertEqual(code, 0, err)
        self.assertEqual(out["totals"]["total_invested"], 0)
        self.assertIsNone(out["holdings"][0]["position"])

    def test_show_of_a_missing_basket_exits_one(self):
        code, _, err = self.run_cli("show", "nope")
        self.assertEqual(code, 1)
        self.assertIn("BASKET_NOT_FOUND", err)

    def test_show_with_prices_reports_value(self):
        slug = self.make_basket(symbols="NVDA:1")
        code, out, err = self.run_cli("show", slug, "--prices", '{"NVDA": 210.0}')
        self.assertEqual(code, 0, err)
        self.assertEqual(out["holdings"][0]["current_price"], 210.0)


class TestSnapshotExport(CliTestCase):

    def test_a_write_creates_the_snapshot_file(self):
        slug = self.make_basket()
        path = self.data_dir / "baskets" / (slug + ".json")
        self.assertTrue(path.exists())
        data = json.loads(path.read_text())
        self.assertEqual(data["slug"], slug)

    def test_deleting_every_snapshot_does_not_change_a_read(self):
        slug = self.make_basket()
        before = self.run_cli("show", slug)[1]
        for path in (self.data_dir / "baskets").glob("*.json"):
            path.unlink()
        after = self.run_cli("show", slug)[1]
        del before["totals"]["built_at"], after["totals"]["built_at"]
        self.assertEqual(before, after)

    def test_export_restores_the_files(self):
        slug = self.make_basket()
        for path in (self.data_dir / "baskets").glob("*.json"):
            path.unlink()
        code, _, err = self.run_cli("export")
        self.assertEqual(code, 0, err)
        self.assertTrue((self.data_dir / "baskets" / (slug + ".json")).exists())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.test_basket_cli -v`
Expected: FAIL. The tool does not exist, so every subprocess returns a non-zero code with a Python traceback about a missing file.

- [ ] **Step 3: Write the implementation**

Create `skills/basket-manager/scripts/basket.py`:

```python
#!/usr/bin/env python3
"""The Tradethos basket command-line tool.

Every write appends to the event log and then writes a snapshot export. No
command reads a snapshot. Section 6 of the design document gives the rules.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from basket_events import CorruptLineError, EventLog, make_event, utc_now
from basket_store import replay, slugify, snapshot_dict
from basket_weights import WeightError, normalize_weights

DEFAULT_DATA_DIR = Path.home() / ".tradethos"

EXIT_OK = 0
EXIT_VALIDATION = 1
EXIT_IO = 2
EXIT_INTEGRITY = 3


class CliError(Exception):
    """An error that the tool reports as JSON on stderr."""

    def __init__(self, message, code, detail=None, exit_code=EXIT_VALIDATION):
        super(CliError, self).__init__(message)
        self.message = message
        self.code = code
        self.detail = detail or {}
        self.exit_code = exit_code


class Store(object):
    """Bind the log and the snapshot directory to one data directory."""

    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.log = EventLog(self.data_dir)
        self.baskets_dir = self.data_dir / "baskets"

    def load(self):
        """Replay the log. Raise a CliError on a corrupt line."""
        try:
            events = self.log.read()
        except CorruptLineError as error:
            raise CliError(str(error), "CORRUPT_LOG_LINE",
                           {"line": error.line_number},
                           exit_code=EXIT_INTEGRITY)
        return replay(events)

    def require(self, slug):
        """Return one basket, or raise a CliError that lists the slugs."""
        result = self.load()
        basket = result.baskets.get(slug)
        if basket is None:
            raise CliError(
                "No basket has the slug %r" % slug, "BASKET_NOT_FOUND",
                {"slug": slug, "available": sorted(result.baskets)})
        return result, basket

    def write(self, events, slugs):
        """Append events, then export the snapshots for the changed baskets."""
        self.log.append(events)
        self.export(slugs)

    def export(self, slugs=None):
        """Write the snapshot file for the named baskets, or for all of them."""
        result = self.load()
        self.baskets_dir.mkdir(parents=True, exist_ok=True)
        written = []
        for slug, basket in result.baskets.items():
            if slugs is not None and slug not in slugs:
                continue
            path = self.baskets_dir / (slug + ".json")
            path.write_text(json.dumps(snapshot_dict(basket), indent=2) + "\n")
            written.append(slug)
        return written


def parse_symbol_weights(text):
    """Parse 'NVDA:2,MSFT:1' into {'NVDA': 2.0, 'MSFT': 1.0}."""
    weights = {}
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            raise CliError(
                "Expected SYMBOL:WEIGHT, got %r" % chunk, "BAD_SYMBOL_LIST",
                {"item": chunk})
        symbol, _, raw = chunk.partition(":")
        symbol = symbol.strip().upper()
        try:
            value = float(raw)
        except ValueError:
            raise CliError(
                "The weight for %s is not a number: %r" % (symbol, raw),
                "BAD_WEIGHT", {"symbol": symbol, "value": raw})
        weights[symbol] = value
    if not weights:
        raise CliError("No symbols given", "BAD_SYMBOL_LIST", {})
    return weights


def parse_prices(text):
    """Parse a --prices JSON object into {symbol: float}."""
    if not text:
        return {}
    try:
        data = json.loads(text)
    except ValueError as error:
        raise CliError("The --prices value is not valid JSON: %s" % error,
                       "BAD_PRICES", {})
    if not isinstance(data, dict):
        raise CliError("The --prices value must be a JSON object",
                       "BAD_PRICES", {})
    return dict((str(k).upper(), float(v)) for k, v in data.items())


def normalize_or_fail(weights):
    """Normalize a weight set and turn a WeightError into a CliError."""
    try:
        return normalize_weights(weights)
    except WeightError as error:
        raise CliError(str(error), "BAD_WEIGHTS", {})


def basket_view(basket, prices=None):
    """Build the JSON view that `show` prints."""
    prices = prices or {}
    data = snapshot_dict(basket)
    total_value = 0.0
    for entry in data["holdings"]:
        price = prices.get(entry["symbol"])
        entry["current_price"] = price
        position = entry["position"]
        if position and price is not None:
            value = position["shares"] * price
            entry["current_value"] = round(value, 2)
            entry["pnl"] = round(value - position["total_invested"], 2)
            total_value += value
        else:
            entry["current_value"] = None
            entry["pnl"] = None
    data["totals"]["current_value"] = round(total_value, 2) if prices else None
    return data


# --- commands ---------------------------------------------------------------

def cmd_create(args, store):
    slug = slugify(args.name)
    if not slug:
        raise CliError("The name gives an empty slug", "BAD_NAME",
                       {"name": args.name})

    raw = parse_symbol_weights(args.symbols)
    normalized = normalize_or_fail(raw)

    with store.log.locked():
        result = store.load()
        if slug in result.baskets:
            raise CliError("A basket already uses the slug %r" % slug,
                           "SLUG_EXISTS", {"slug": slug})

        events = [make_event(
            "basket_created", slug, name=args.name,
            description=args.description or "",
            account_number=args.account,
            rebalance_threshold_pct=args.threshold)]
        for symbol in raw:
            events.append(make_event(
                "holding_added", slug, symbol=symbol,
                weight=normalized[symbol], thesis=""))
        store.write(events, {slug})

    _, basket = store.require(slug)
    view = snapshot_dict(basket)
    view["normalized"] = {s: normalized[s] for s in raw if raw[s] != normalized[s]}
    return view


def cmd_list(args, store):
    result = store.load()
    rows = []
    for slug, basket in result.baskets.items():
        rows.append({
            "slug": slug,
            "name": basket.name,
            "holdings": len(basket.holdings),
            "total_invested": round(basket.total_invested, 2),
            "realized_pnl": round(basket.realized_pnl, 2),
        })
    return {"baskets": rows}


def cmd_show(args, store):
    _, basket = store.require(args.slug)
    if not basket.holdings:
        raise CliError("The basket %r holds no symbols. Add a holding first."
                       % args.slug, "EMPTY_BASKET", {"slug": args.slug})
    return basket_view(basket, parse_prices(args.prices))


def cmd_history(args, store):
    try:
        events = store.log.read()
    except CorruptLineError as error:
        raise CliError(str(error), "CORRUPT_LOG_LINE",
                       {"line": error.line_number}, exit_code=EXIT_INTEGRITY)
    rows = []
    for event in events:
        if args.slug and event.get("slug") != args.slug:
            continue
        if args.symbol and event.get("symbol") != args.symbol.upper():
            continue
        if args.since and event.get("ts", "") < args.since:
            continue
        rows.append(event)
    return {"events": rows}


def cmd_export(args, store):
    slugs = {args.slug} if args.slug else None
    return {"exported": store.export(slugs)}


# --- output -----------------------------------------------------------------

def print_table(command, payload):
    """Print a human-readable view for the commands that have one."""
    if command == "list":
        print("%-28s %-10s %12s" % ("Basket", "Holdings", "Invested"))
        print("-" * 52)
        for row in payload["baskets"]:
            print("%-28s %-10d %12.2f" % (
                row["name"], row["holdings"], row["total_invested"]))
        return
    print(json.dumps(payload, indent=2))


def build_parser():
    # --data-dir and --format must work before and after the subcommand.
    # argparse hands everything after the subcommand name to the subparser, so
    # an option defined only on the root parser fails there with exit code 2.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data-dir", default=None, help=argparse.SUPPRESS)
    common.add_argument("--format", choices=["json", "table"], default="json")

    parser = argparse.ArgumentParser(
        prog="basket.py", parents=[common],
        description="Manage local stock baskets")
    sub = parser.add_subparsers(dest="command")

    def add(name, help_text):
        return sub.add_parser(name, parents=[common], help=help_text)

    p = add("create", "Create a basket")
    p.add_argument("name")
    p.add_argument("--symbols", required=True, help="NVDA:2,MSFT:1")
    p.add_argument("--description", default="")
    p.add_argument("--account", required=True,
                   help="The brokerage account that will hold these trades")
    p.add_argument("--threshold", type=float, default=5.0)
    p.set_defaults(func=cmd_create)

    p = add("list", "List every basket")
    p.set_defaults(func=cmd_list)

    p = add("show", "Show one basket")
    p.add_argument("slug")
    p.add_argument("--prices", default="")
    p.set_defaults(func=cmd_show)

    p = add("history", "Print the events of a basket")
    p.add_argument("slug", nargs="?", default=None)
    p.add_argument("--symbol", default=None)
    p.add_argument("--since", default=None)
    p.set_defaults(func=cmd_history)

    p = add("export", "Write the snapshot files")
    p.add_argument("slug", nargs="?", default=None)
    p.set_defaults(func=cmd_export)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return EXIT_VALIDATION

    data_dir = Path(args.data_dir) if args.data_dir else DEFAULT_DATA_DIR
    store = Store(data_dir)

    try:
        payload = args.func(args, store)
    except CliError as error:
        sys.stderr.write(json.dumps({
            "error": error.message,
            "code": error.code,
            "detail": error.detail,
        }) + "\n")
        return error.exit_code
    except OSError as error:
        sys.stderr.write(json.dumps({
            "error": str(error), "code": "IO_ERROR", "detail": {},
        }) + "\n")
        return EXIT_IO

    if args.format == "table":
        print_table(args.command, payload)
    else:
        print(json.dumps(payload, indent=2))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m unittest tests.test_basket_cli -v`
Expected: PASS, 15 tests.

- [ ] **Step 5: Run the whole suite**

Run: `python3 -m unittest discover -s tests`
Expected: OK, 119 tests.

- [ ] **Step 6: Commit**

```bash
git add skills/basket-manager/scripts/basket.py tests/test_basket_cli.py
git commit -m "feat(basket): add the CLI with create, list, show, history, export

Every write appends events and then exports a snapshot. No command reads
a snapshot, so deleting the export directory changes no read."
```

---

## Task 5: Weight commands

**Files:**
- Modify: `skills/basket-manager/scripts/basket.py` (add subcommands and helpers)
- Modify: `tests/test_basket_cli.py` (add a test class)

**Interfaces:**
- Consumes: `basket_weights.refill`, `basket_weights.FILL_MODES`, `Store`, `CliError`.
- Produces:
  - `plan_weight_change(basket, changes, fill_mode) -> dict` — returns the complete new weight set without writing.
  - Subcommands `set-weight`, `set-weights`, `add-holding`, `remove-holding`, each accepting `--dry-run`; `set-weight` and `add-holding` and `remove-holding` also accept `--fill {proportional,equal}`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_basket_cli.py`, before the `if __name__` block:

```python
class TestWeightCommands(CliTestCase):

    def three_holding_basket(self):
        code, out, err = self.run_cli(
            "create", "Trio", "--symbols", "NVDA:50,MSFT:30,AAPL:20",
            "--account", "000000000")
        self.assertEqual(code, 0, err)
        return out["slug"]

    def weights(self, slug):
        out = self.run_cli("show", slug)[1]
        return dict((h["symbol"], h["target_weight_pct"]) for h in out["holdings"])

    def test_set_weight_proportional_is_the_default(self):
        slug = self.three_holding_basket()
        code, out, err = self.run_cli("set-weight", slug, "--symbol", "NVDA",
                                      "--weight", "20")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.weights(slug), {"NVDA": 20, "MSFT": 48, "AAPL": 32})

    def test_set_weight_equal_fill(self):
        slug = self.three_holding_basket()
        code, _, err = self.run_cli("set-weight", slug, "--symbol", "NVDA",
                                    "--weight", "20", "--fill", "equal")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.weights(slug), {"NVDA": 20, "MSFT": 40, "AAPL": 40})

    def test_dry_run_writes_nothing(self):
        slug = self.three_holding_basket()
        before = self.run_cli("history", slug)[1]["events"]
        code, out, err = self.run_cli("set-weight", slug, "--symbol", "NVDA",
                                      "--weight", "20", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertTrue(out["dry_run"])
        after = self.run_cli("history", slug)[1]["events"]
        self.assertEqual(len(before), len(after))
        self.assertEqual(self.weights(slug)["NVDA"], 50)

    def test_dry_run_returns_the_complete_set(self):
        slug = self.three_holding_basket()
        out = self.run_cli("set-weight", slug, "--symbol", "NVDA",
                           "--weight", "20", "--dry-run")[1]
        self.assertEqual(out["weights"], {"NVDA": 20, "MSFT": 48, "AAPL": 32})

    def test_set_weights_needs_every_holding(self):
        slug = self.three_holding_basket()
        code, _, err = self.run_cli("set-weights", slug, "--weights", "NVDA:50,MSFT:50")
        self.assertEqual(code, 1)
        self.assertIn("AAPL", err)

    def test_set_weights_refuses_an_unknown_symbol(self):
        slug = self.three_holding_basket()
        code, _, err = self.run_cli(
            "set-weights", slug, "--weights", "NVDA:40,MSFT:30,AAPL:20,TSLA:10")
        self.assertEqual(code, 1)
        self.assertIn("TSLA", err)

    def test_set_weights_normalizes(self):
        slug = self.three_holding_basket()
        code, _, err = self.run_cli("set-weights", slug,
                                    "--weights", "NVDA:1,MSFT:1,AAPL:1")
        self.assertEqual(code, 0, err)
        self.assertEqual(sum(self.weights(slug).values()), 100)

    def test_set_weights_writes_only_changed_holdings(self):
        slug = self.three_holding_basket()
        self.run_cli("set-weights", slug, "--weights", "NVDA:50,MSFT:30,AAPL:20")
        events = self.run_cli("history", slug)[1]["events"]
        changed = [e for e in events if e["type"] == "weight_changed"]
        self.assertEqual(changed, [])

    def test_add_holding_scales_the_others_down(self):
        slug = self.three_holding_basket()
        code, _, err = self.run_cli("add-holding", slug, "--symbol", "TSLA",
                                    "--weight", "20")
        self.assertEqual(code, 0, err)
        weights = self.weights(slug)
        self.assertEqual(weights["TSLA"], 20)
        self.assertEqual(sum(weights.values()), 100)

    def test_remove_holding_scales_the_others_up(self):
        slug = self.three_holding_basket()
        code, _, err = self.run_cli("remove-holding", slug, "--symbol", "AAPL")
        self.assertEqual(code, 0, err)
        weights = self.weights(slug)
        self.assertNotIn("AAPL", weights)
        self.assertEqual(sum(weights.values()), 100)

    def test_every_command_leaves_the_total_at_one_hundred(self):
        slug = self.three_holding_basket()
        for args in (
            ("set-weight", slug, "--symbol", "NVDA", "--weight", "70"),
            ("add-holding", slug, "--symbol", "TSLA", "--weight", "10"),
            ("set-weights", slug, "--weights", "NVDA:1,MSFT:1,AAPL:1,TSLA:1"),
            ("remove-holding", slug, "--symbol", "TSLA"),
        ):
            code, _, err = self.run_cli(*args)
            self.assertEqual(code, 0, err)
            self.assertEqual(sum(self.weights(slug).values()), 100, args)

    def test_a_result_that_would_fall_to_zero_is_refused(self):
        slug = self.three_holding_basket()
        code, _, err = self.run_cli("set-weight", slug, "--symbol", "NVDA",
                                    "--weight", "99")
        self.assertEqual(code, 1)
        self.assertIn("1 percent cannot cover 2 holdings", err)

    def test_set_name_does_not_change_the_slug(self):
        slug = self.three_holding_basket()
        code, _, err = self.run_cli("set-name", slug, "--name", "Renamed")
        self.assertEqual(code, 0, err)
        out = self.run_cli("show", slug)[1]
        self.assertEqual(out["slug"], slug)
        self.assertEqual(out["name"], "Renamed")

    def test_remove_holding_proceeds_when_no_shares_are_held(self):
        # The refusal path needs a recorded fill, so Task 6 covers it.
        slug = self.three_holding_basket()
        code, _, err = self.run_cli("remove-holding", slug, "--symbol", "NVDA")
        self.assertEqual(code, 0, err)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.test_basket_cli.TestWeightCommands -v`
Expected: FAIL. `argparse` reports `invalid choice: 'set-weight'`.

- [ ] **Step 3: Add the planner and the commands**

First change the `basket_weights` import at the top of `basket.py` to:

```python
from basket_weights import FILL_MODES, WeightError, normalize_weights, refill
```

Then add these helpers after `normalize_or_fail`:

```python
def refill_or_fail(others, room, mode):
    """Call refill and turn a WeightError into a CliError."""
    try:
        return refill(others, room, mode)
    except WeightError as error:
        raise CliError(str(error), "BAD_WEIGHTS", {})


def check_target_weight(weight, symbol):
    """Reject a target weight outside 1 to 100.

    argparse accepts any integer, so 0 and negatives reach the command. A
    weight of 0 means the user wants the holding gone, and remove-holding is
    the command for that.
    """
    if weight < 1 or weight > 100:
        raise CliError(
            "A target weight must be between 1 and 100, but %s was given %d. "
            "Use remove-holding to drop a holding." % (symbol, weight),
            "BAD_WEIGHTS", {"symbol": symbol, "weight": weight})


def plan_weight_change(basket, target_symbol, target_weight, fill_mode,
                       removing=False):
    """Return the complete weight set after a single-holding change.

    The named holding takes its weight. Every other holding shares the rest,
    by the fill mode. The result always sums to 100.

    With removing=True the named holding leaves, and the remaining holdings
    share the whole 100 percent.
    """
    others = dict((s, w) for s, w in basket.target_weights.items()
                  if s != target_symbol)

    if removing:
        if not others:
            return {}
        return refill_or_fail(others, 100, fill_mode)

    if not others:
        return {target_symbol: 100}

    room = 100 - int(target_weight)
    if room < 1:
        raise CliError(
            "A weight of %d leaves nothing for the other holdings"
            % int(target_weight), "BAD_WEIGHTS", {"symbol": target_symbol})

    filled = refill_or_fail(others, room, fill_mode)
    filled[target_symbol] = int(target_weight)
    return filled


def weight_events(basket, new_weights):
    """Build a weight_changed event for each holding whose weight moved."""
    events = []
    for symbol, weight in new_weights.items():
        holding = basket.holdings.get(symbol)
        if holding is None or holding.target_weight_pct == weight:
            continue
        events.append(make_event(
            "weight_changed", basket.slug, symbol=symbol,
            **{"from": holding.target_weight_pct, "to": weight}))
    return events
```

Add the command functions:

```python
def _apply_weights(store, args, slug, basket, new_weights, extra_events=()):
    """Write one batch of events, or return the dry-run view.

    Every weight command appends exactly once. Two appends would leave a
    window in which the log breaks the sum-to-100 invariant that section 6.5
    promises can never happen.
    """
    events = list(extra_events) + weight_events(basket, new_weights)
    if args.dry_run:
        return {"dry_run": True, "slug": slug, "weights": new_weights,
                "events": len(events)}
    if events:
        store.write(events, {slug})
    return {"dry_run": False, "slug": slug, "weights": new_weights}


def cmd_set_weight(args, store):
    with store.log.locked():
        _, basket = store.require(args.slug)
        symbol = args.symbol.upper()
        if symbol not in basket.holdings:
            raise CliError("The basket does not hold %s" % symbol,
                           "SYMBOL_NOT_FOUND", {"symbol": symbol})
        check_target_weight(args.weight, symbol)
        new_weights = plan_weight_change(basket, symbol, args.weight, args.fill)
        return _apply_weights(store, args, args.slug, basket, new_weights)


def cmd_set_weights(args, store):
    with store.log.locked():
        _, basket = store.require(args.slug)
        given = parse_symbol_weights(args.weights)

        missing = sorted(set(basket.holdings) - set(given))
        if missing:
            raise CliError(
                "These holdings have no weight: %s" % ", ".join(missing),
                "MISSING_WEIGHTS", {"missing": missing})
        unknown = sorted(set(given) - set(basket.holdings))
        if unknown:
            raise CliError(
                "The basket does not hold: %s" % ", ".join(unknown),
                "SYMBOL_NOT_FOUND", {"unknown": unknown})

        return _apply_weights(store, args, args.slug, basket,
                              normalize_or_fail(given))


def cmd_add_holding(args, store):
    with store.log.locked():
        _, basket = store.require(args.slug)
        symbol = args.symbol.upper()
        if symbol in basket.holdings:
            raise CliError("The basket already holds %s" % symbol,
                           "SYMBOL_EXISTS", {"symbol": symbol})
        if len(basket.holdings) + 1 > 100:
            raise CliError("A basket holds at most 100 holdings",
                           "TOO_MANY_HOLDINGS", {})
        check_target_weight(args.weight, symbol)

        new_weights = plan_weight_change(basket, symbol, args.weight, args.fill)
        added = make_event("holding_added", args.slug, symbol=symbol,
                           weight=int(args.weight), thesis=args.thesis or "")
        # weight_events skips the new symbol, because the basket does not hold
        # it yet. The holding_added event carries its weight instead.
        return _apply_weights(store, args, args.slug, basket, new_weights,
                              extra_events=[added])


def cmd_remove_holding(args, store):
    with store.log.locked():
        _, basket = store.require(args.slug)
        symbol = args.symbol.upper()
        holding = basket.holdings.get(symbol)
        if holding is None:
            raise CliError("The basket does not hold %s" % symbol,
                           "SYMBOL_NOT_FOUND", {"symbol": symbol})
        if holding.has_position and not args.force:
            raise CliError(
                "%s still holds %s shares. Sell them first, or pass --force."
                % (symbol, holding.position.shares),
                "HOLDING_HAS_POSITION",
                {"symbol": symbol, "shares": holding.position.shares})

        new_weights = plan_weight_change(basket, symbol, 0, args.fill,
                                         removing=True)
        removed = make_event("holding_removed", args.slug, symbol=symbol)
        # new_weights omits the removed symbol, so weight_events never emits
        # an event for it.
        return _apply_weights(store, args, args.slug, basket, new_weights,
                              extra_events=[removed])


def cmd_set_name(args, store):
    with store.log.locked():
        _, basket = store.require(args.slug)
        event = make_event("basket_updated", args.slug, field="name",
                           **{"from": basket.name, "to": args.name})
        store.write([event], {args.slug})
    return {"slug": args.slug, "name": args.name}


def cmd_set_description(args, store):
    with store.log.locked():
        _, basket = store.require(args.slug)
        event = make_event("basket_updated", args.slug, field="description",
                           **{"from": basket.description, "to": args.description})
        store.write([event], {args.slug})
    return {"slug": args.slug, "description": args.description}


def cmd_set_threshold(args, store):
    with store.log.locked():
        _, basket = store.require(args.slug)
        event = make_event(
            "basket_updated", args.slug, field="rebalance_threshold_pct",
            **{"from": basket.rebalance_threshold_pct, "to": args.threshold})
        store.write([event], {args.slug})
    return {"slug": args.slug, "rebalance_threshold_pct": args.threshold}


def cmd_set_thesis(args, store):
    with store.log.locked():
        _, basket = store.require(args.slug)
        symbol = args.symbol.upper()
        if symbol not in basket.holdings:
            raise CliError("The basket does not hold %s" % symbol,
                           "SYMBOL_NOT_FOUND", {"symbol": symbol})
        event = make_event("thesis_changed", args.slug, symbol=symbol,
                           thesis=args.thesis)
        store.write([event], {args.slug})
    return {"slug": args.slug, "symbol": symbol, "thesis": args.thesis}


def cmd_delete(args, store):
    with store.log.locked():
        _, basket = store.require(args.slug)
        held = [h.symbol for h in basket.holdings.values() if h.has_position]
        if held and not args.force:
            raise CliError(
                "The basket still holds %s. Deletion removes the record only; "
                "it does not sell any stock. Pass --force to delete it."
                % ", ".join(held),
                "BASKET_HAS_POSITIONS",
                {"symbols": held, "total_invested": basket.total_invested})
        store.log.append([make_event("basket_deleted", args.slug)])
        maybe_backup(store)

    path = store.baskets_dir / (args.slug + ".json")
    if path.exists():
        path.unlink()
    return {"deleted": args.slug}
```

Add the subparsers inside `build_parser`, before `return parser`:

```python
    def add_dry_run(p):
        p.add_argument("--dry-run", action="store_true")

    def add_fill(p):
        p.add_argument("--fill", choices=list(FILL_MODES), default=FILL_MODES[0])

    p = add("set-weight", "Set one target weight")
    p.add_argument("slug")
    p.add_argument("--symbol", required=True)
    p.add_argument("--weight", type=int, required=True)
    add_fill(p)
    add_dry_run(p)
    p.set_defaults(func=cmd_set_weight)

    p = add("set-weights", "Set every target weight")
    p.add_argument("slug")
    p.add_argument("--weights", required=True)
    add_dry_run(p)
    p.set_defaults(func=cmd_set_weights)

    p = add("add-holding", "Add a symbol")
    p.add_argument("slug")
    p.add_argument("--symbol", required=True)
    p.add_argument("--weight", type=int, required=True)
    p.add_argument("--thesis", default="")
    add_fill(p)
    add_dry_run(p)
    p.set_defaults(func=cmd_add_holding)

    p = add("remove-holding", "Remove a symbol")
    p.add_argument("slug")
    p.add_argument("--symbol", required=True)
    p.add_argument("--force", action="store_true")
    add_fill(p)
    add_dry_run(p)
    p.set_defaults(func=cmd_remove_holding)

    p = add("set-thesis", "Change a thesis")
    p.add_argument("slug")
    p.add_argument("--symbol", required=True)
    p.add_argument("--thesis", required=True)
    p.set_defaults(func=cmd_set_thesis)

    p = add("set-name", "Change the display name")
    p.add_argument("slug")
    p.add_argument("--name", required=True)
    p.set_defaults(func=cmd_set_name)

    p = add("set-description", "Change the description")
    p.add_argument("slug")
    p.add_argument("--description", required=True)
    p.set_defaults(func=cmd_set_description)

    p = add("set-threshold", "Change the rebalance threshold")
    p.add_argument("slug")
    p.add_argument("--threshold", type=float, required=True)
    p.set_defaults(func=cmd_set_threshold)

    p = add("delete", "Delete a basket")
    p.add_argument("slug")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_delete)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m unittest tests.test_basket_cli.TestWeightCommands -v`
Expected: PASS, 14 tests.

- [ ] **Step 5: Run the whole suite**

Run: `python3 -m unittest discover -s tests`
Expected: OK, 133 tests.

- [ ] **Step 6: Commit**

```bash
git add skills/basket-manager/scripts/basket.py tests/test_basket_cli.py
git commit -m "feat(basket): add the weight commands

set-weight, set-weights, add-holding, and remove-holding keep the total
at exactly 100. --fill chooses proportional or equal distribution, and
--dry-run returns the complete set without writing."
```

---

## Task 6: record-fills

**Files:**
- Modify: `skills/basket-manager/scripts/basket.py`
- Modify: `tests/test_basket_cli.py`

**Interfaces:**
- Consumes: `Store`, `CliError`, `basket_store.replay`.
- Produces:
  - `orders_from_response(payload) -> dict` — maps `order_id` to the order dict, accepting `{"data": {"orders": [...]}}`, `{"orders": [...]}`, or a bare list.
  - Subcommand `record-fills` with `--orders-json`, `--order-ids`, `--account`, `--cap-at-held`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_basket_cli.py`:

```python
def order(order_id="o1", symbol="NVDA", side="buy", quantity="10",
          average_price="50.00", state="filled", price="49.00"):
    """Build an order in the shape that get_equity_orders returns."""
    return {
        "id": order_id, "symbol": symbol, "side": side, "state": state,
        "quantity": quantity, "cumulative_quantity": quantity,
        "price": price, "average_price": average_price,
        "dollar_based_amount": {"amount": "10.00", "currency_code": "USD"},
        "created_at": "2026-07-23T19:25:22.952062Z",
        "last_transaction_at": "2026-07-23T19:25:23.115Z",
        "executions": [{"price": average_price, "quantity": quantity}],
    }


def orders_response(*orders):
    return json.dumps({"data": {"orders": list(orders)}})


class TestRecordFills(CliTestCase):

    def setUp(self):
        CliTestCase.setUp(self)
        self.slug = self.make_basket(name="Trio", symbols="NVDA:50,MSFT:50")

    def record(self, response, ids, account="000000000", *extra):
        return self.run_cli("record-fills", self.slug, "--orders-json", response,
                            "--order-ids", ids, "--account", account, *extra)

    def test_records_one_order(self):
        code, out, err = self.record(orders_response(order()), "o1")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["recorded"], ["o1"])
        shown = self.run_cli("show", self.slug)[1]
        position = [h for h in shown["holdings"] if h["symbol"] == "NVDA"][0]["position"]
        self.assertEqual(position["shares"], 10.0)

    def test_reads_average_price_not_price(self):
        code, _, err = self.record(
            orders_response(order(average_price="208.04", price="206.80")), "o1")
        self.assertEqual(code, 0, err)
        shown = self.run_cli("show", self.slug)[1]
        position = [h for h in shown["holdings"] if h["symbol"] == "NVDA"][0]["position"]
        self.assertEqual(position["avg_cost"], 208.04)

    def test_records_only_the_listed_ids(self):
        response = orders_response(order(order_id="mine"),
                                   order(order_id="theirs", quantity="99"))
        code, out, err = self.record(response, "mine")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["recorded"], ["mine"])
        shown = self.run_cli("show", self.slug)[1]
        position = [h for h in shown["holdings"] if h["symbol"] == "NVDA"][0]["position"]
        self.assertEqual(position["shares"], 10.0)

    def test_reads_the_side_of_each_order(self):
        buy_then_sell = orders_response(
            order(order_id="b1", side="buy", quantity="10", average_price="50.00"),
            order(order_id="s1", side="sell", quantity="4", average_price="70.00"))
        code, out, err = self.record(buy_then_sell, "b1,s1")
        self.assertEqual(code, 0, err)
        shown = self.run_cli("show", self.slug)[1]
        position = [h for h in shown["holdings"] if h["symbol"] == "NVDA"][0]["position"]
        self.assertEqual(position["shares"], 6.0)
        self.assertEqual(position["realized_pnl"], 80.0)

    def test_a_repeated_call_changes_nothing(self):
        response = orders_response(order())
        self.record(response, "o1")
        code, out, err = self.record(response, "o1")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["already_recorded"], ["o1"])
        shown = self.run_cli("show", self.slug)[1]
        position = [h for h in shown["holdings"] if h["symbol"] == "NVDA"][0]["position"]
        self.assertEqual(position["shares"], 10.0)

    def test_an_id_in_another_basket_is_skipped(self):
        other = self.make_basket(name="Other", symbols="NVDA:100")
        self.record(orders_response(order()), "o1")
        code, _, err = self.run_cli("record-fills", other, "--orders-json",
                                    orders_response(order()), "--order-ids", "o1",
                                    "--account", "000000000")
        # Nothing else was in the batch, so nothing was recorded.
        self.assertEqual(code, 1)
        self.assertIn("ORDER_IN_OTHER_BASKET", err)

    def test_a_batch_keeps_its_good_fills_when_one_id_is_taken(self):
        other = self.make_basket(name="Other", symbols="NVDA:50,MSFT:50")
        self.record(orders_response(order(order_id="taken")), "taken")
        batch = orders_response(
            order(order_id="taken"),
            order(order_id="fresh", symbol="MSFT", quantity="4",
                  average_price="25.00"))
        code, out, err = self.run_cli(
            "record-fills", other, "--orders-json", batch,
            "--order-ids", "taken,fresh", "--account", "000000000")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["recorded"], ["fresh"])
        reasons = [s["reason"] for s in out["skipped"]]
        self.assertIn("ORDER_IN_OTHER_BASKET", reasons)

    def test_a_wrong_account_is_refused(self):
        code, _, err = self.record(orders_response(order()), "o1", "999999")
        self.assertEqual(code, 1)
        self.assertIn("ACCOUNT_MISMATCH", err)

    def test_a_mixed_batch_records_the_good_orders(self):
        self.record(orders_response(order(order_id="b1", quantity="10")), "b1")
        batch = orders_response(
            order(order_id="b2", symbol="MSFT", quantity="5", average_price="20.00"),
            order(order_id="s9", side="sell", quantity="999", average_price="60.00"))
        code, out, err = self.record(batch, "b2,s9")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["recorded"], ["b2"])
        self.assertEqual(len(out["skipped"]), 1)
        self.assertEqual(out["skipped"][0]["order_id"], "s9")

    def test_recording_no_order_exits_one(self):
        batch = orders_response(
            order(order_id="s9", side="sell", quantity="999", average_price="60.00"))
        code, _, err = self.record(batch, "s9")
        self.assertEqual(code, 1)
        self.assertIn("NOTHING_RECORDED", err)

    def test_an_unfilled_order_is_skipped_then_recorded_later(self):
        pending = orders_response(order(order_id="p1", state="confirmed"))
        code, out, _ = self.record(pending, "p1")
        self.assertEqual(code, 1)
        filled = orders_response(order(order_id="p1", state="filled"))
        code, out, err = self.record(filled, "p1")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["recorded"], ["p1"])

    def test_cap_at_held_records_the_held_shares(self):
        self.record(orders_response(order(order_id="b1", quantity="10")), "b1")
        big_sell = orders_response(
            order(order_id="s1", side="sell", quantity="25", average_price="70.00"))
        code, _, _ = self.record(big_sell, "s1")
        self.assertEqual(code, 1)
        code, out, err = self.record(big_sell, "s1", "000000000", "--cap-at-held")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["capped"][0]["recorded_shares"], 10.0)
        shown = self.run_cli("show", self.slug)[1]
        position = [h for h in shown["holdings"] if h["symbol"] == "NVDA"][0]["position"]
        self.assertEqual(position["shares"], 0.0)

    def test_cap_at_held_is_refused_for_more_than_one_id(self):
        response = orders_response(order(order_id="a"), order(order_id="b"))
        code, _, err = self.record(response, "a,b", "000000000", "--cap-at-held")
        self.assertEqual(code, 1)
        self.assertIn("CAP_NEEDS_ONE_ORDER", err)

    def test_an_unknown_id_is_reported(self):
        code, _, err = self.record(orders_response(order()), "missing")
        self.assertEqual(code, 1)
        self.assertIn("ORDER_NOT_IN_RESPONSE", err)

    def test_remove_holding_refuses_a_held_position(self):
        self.record(orders_response(order()), "o1")
        code, _, err = self.run_cli("remove-holding", self.slug, "--symbol", "NVDA")
        self.assertEqual(code, 1)
        self.assertIn("HOLDING_HAS_POSITION", err)

    def test_remove_holding_force_removes_a_held_position(self):
        self.record(orders_response(order()), "o1")
        code, _, err = self.run_cli("remove-holding", self.slug,
                                    "--symbol", "NVDA", "--force")
        self.assertEqual(code, 0, err)
        shown = self.run_cli("show", self.slug)[1]
        self.assertEqual([h["symbol"] for h in shown["holdings"]], ["MSFT"])
        self.assertEqual(shown["holdings"][0]["target_weight_pct"], 100)

    def test_delete_refuses_a_basket_that_holds_shares(self):
        self.record(orders_response(order()), "o1")
        code, _, err = self.run_cli("delete", self.slug)
        self.assertEqual(code, 1)
        self.assertIn("BASKET_HAS_POSITIONS", err)

    def test_delete_force_removes_the_basket(self):
        self.record(orders_response(order()), "o1")
        code, _, err = self.run_cli("delete", self.slug, "--force")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.run_cli("list")[1]["baskets"], [])

    def test_a_zero_target_weight_is_refused(self):
        code, _, err = self.run_cli("set-weight", self.slug, "--symbol", "NVDA",
                                    "--weight", "0")
        self.assertEqual(code, 1)
        self.assertIn("between 1 and 100", err)

    def test_a_negative_target_weight_is_refused(self):
        code, _, err = self.run_cli("set-weight", self.slug, "--symbol", "NVDA",
                                    "--weight", "-5")
        self.assertEqual(code, 1)
        self.assertIn("between 1 and 100", err)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.test_basket_cli.TestRecordFills -v`
Expected: FAIL. `argparse` reports `invalid choice: 'record-fills'`.

- [ ] **Step 3: Add the implementation**

Add to `basket.py`:

```python
def orders_from_response(payload):
    """Return {order_id: order} from a get_equity_orders response.

    The live response nests the list under a `data` key. A bare list and a
    single-level envelope also work.
    """
    try:
        data = json.loads(payload)
    except ValueError as error:
        raise CliError("The --orders-json value is not valid JSON: %s" % error,
                       "BAD_ORDERS_JSON", {})

    if isinstance(data, dict) and "data" in data:
        data = data["data"]
    if isinstance(data, dict):
        for key in ("orders", "results"):
            if key in data:
                data = data[key]
                break
    if not isinstance(data, list):
        raise CliError(
            "Expected a list of orders. Pass the get_equity_orders response.",
            "BAD_ORDERS_JSON", {})

    index = {}
    for entry in data:
        if isinstance(entry, dict) and entry.get("id"):
            index[entry["id"]] = entry
    return index


def _order_shares(order):
    """Return the filled share count of an order."""
    for key in ("cumulative_quantity", "quantity"):
        raw = order.get(key)
        if raw in (None, ""):
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0.0


def _order_price(order):
    """Return the fill price of an order.

    The tool reads `average_price`. It never reads `price`, which holds the
    limit price. A weighted mean of the executions is the only fallback.
    """
    raw = order.get("average_price")
    if raw not in (None, ""):
        try:
            value = float(raw)
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass

    total_shares = 0.0
    total_cost = 0.0
    for execution in order.get("executions") or []:
        if not isinstance(execution, dict):
            continue
        try:
            shares = float(execution.get("quantity") or 0)
            price = float(execution.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if shares > 0 and price > 0:
            total_shares += shares
            total_cost += shares * price
    return total_cost / total_shares if total_shares > 0 else 0.0


def cmd_record_fills(args, store):
    order_ids = [i.strip() for i in args.order_ids.split(",") if i.strip()]
    if not order_ids:
        raise CliError("No order ids given", "NO_ORDER_IDS", {})
    if args.cap_at_held and len(order_ids) != 1:
        raise CliError(
            "--cap-at-held needs exactly one order id, but %d were given"
            % len(order_ids), "CAP_NEEDS_ONE_ORDER", {"count": len(order_ids)})

    index = orders_from_response(args.orders_json)

    with store.log.locked():
        result, basket = store.require(args.slug)

        if basket.account_number and args.account != basket.account_number:
            raise CliError(
                "The basket uses account %s, but the orders came from %s"
                % (basket.account_number, args.account),
                "ACCOUNT_MISMATCH",
                {"basket_account": basket.account_number, "given": args.account})

        events = []
        recorded = []
        already = []
        skipped = []
        capped = []
        # Track shares within this batch so two sells in one call cannot
        # oversell the same holding.
        pending = dict((s, h.position.shares) for s, h in basket.holdings.items())

        for order_id in order_ids:
            order = index.get(order_id)
            if order is None:
                skipped.append({"order_id": order_id,
                                "reason": "ORDER_NOT_IN_RESPONSE"})
                continue
            if order.get("state") != "filled":
                skipped.append({"order_id": order_id, "reason": "NOT_FILLED",
                                "state": order.get("state")})
                continue

            owner = result.order_index.get(order_id)
            if owner == args.slug:
                already.append(order_id)
                continue
            if owner is not None:
                # Skip, do not abort. Section 6.2 says a batch keeps its good
                # fills; the exit-code table then gives exit 1 only when the
                # tool recorded nothing at all.
                skipped.append({"order_id": order_id,
                                "reason": "ORDER_IN_OTHER_BASKET",
                                "basket": owner})
                continue

            symbol = (order.get("symbol") or "").upper()
            if symbol not in basket.holdings:
                skipped.append({"order_id": order_id, "reason": "SYMBOL_NOT_IN_BASKET",
                                "symbol": symbol})
                continue

            shares = _order_shares(order)
            price = _order_price(order)
            if shares <= 0 or price <= 0:
                skipped.append({"order_id": order_id, "reason": "NO_SHARES_OR_PRICE"})
                continue

            side = (order.get("side") or "").lower()
            if side not in ("buy", "sell"):
                skipped.append({"order_id": order_id, "reason": "UNKNOWN_SIDE",
                                "side": side})
                continue

            if side == "sell":
                held = pending.get(symbol, 0.0)
                if shares > held + 1e-12:
                    if not args.cap_at_held:
                        skipped.append({
                            "order_id": order_id, "reason": "OVERSELL",
                            "symbol": symbol, "held": held, "requested": shares})
                        continue
                    capped.append({"order_id": order_id, "symbol": symbol,
                                   "order_shares": shares,
                                   "recorded_shares": held})
                    shares = held
                if shares <= 0:
                    skipped.append({"order_id": order_id, "reason": "OVERSELL",
                                    "symbol": symbol, "held": held,
                                    "requested": shares})
                    continue
                pending[symbol] = held - shares
            else:
                pending[symbol] = pending.get(symbol, 0.0) + shares

            events.append(make_event(
                order.get("side").lower(), args.slug, symbol=symbol,
                shares=shares, price=price, amount=shares * price,
                order_id=order_id,
                ts=order.get("last_transaction_at") or order.get("created_at")))
            recorded.append(order_id)

        if not recorded and not already:
            raise CliError(
                "No order was recorded", "NOTHING_RECORDED",
                {"skipped": skipped})

        if events:
            store.write(events, {args.slug})

    _, basket = store.require(args.slug)
    return {
        "slug": args.slug,
        "recorded": recorded,
        "already_recorded": already,
        "skipped": skipped,
        "capped": capped,
        "holdings": snapshot_dict(basket)["holdings"],
    }
```

Add the subparser inside `build_parser`:

```python
    p = add("record-fills", "Record filled orders")
    p.add_argument("slug")
    p.add_argument("--orders-json", required=True)
    p.add_argument("--order-ids", required=True)
    p.add_argument("--account", required=True)
    p.add_argument("--cap-at-held", action="store_true")
    p.set_defaults(func=cmd_record_fills)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m unittest tests.test_basket_cli.TestRecordFills -v`
Expected: PASS, 21 tests.

- [ ] **Step 5: Run the whole suite**

Run: `python3 -m unittest discover -s tests`
Expected: OK, 154 tests.

- [ ] **Step 6: Commit**

```bash
git add skills/basket-manager/scripts/basket.py tests/test_basket_cli.py
git commit -m "feat(basket): add record-fills

Records only the listed order ids, reads average_price and never the
limit price, skips a bad order instead of refusing the batch, and caps a
mixed sale at the held shares with --cap-at-held."
```

---

## Task 7: plan-buy and plan-sell

**Files:**
- Modify: `skills/basket-manager/scripts/basket.py`
- Modify: `tests/test_basket_cli.py`

**Interfaces:**
- Consumes: `Store`, `CliError`, `parse_prices`.
- Produces: subcommands `plan-buy` and `plan-sell`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_basket_cli.py`:

```python
class TestPlanning(CliTestCase):

    def setUp(self):
        CliTestCase.setUp(self)
        self.slug = self.make_basket(name="Pair", symbols="NVDA:60,MSFT:40")

    def test_plan_buy_allocates_the_full_amount(self):
        code, out, err = self.run_cli("plan-buy", self.slug, "--amount", "100")
        self.assertEqual(code, 0, err)
        amounts = dict((r["symbol"], r["amount"]) for r in out["orders"])
        self.assertEqual(amounts, {"NVDA": 60.0, "MSFT": 40.0})
        self.assertEqual(round(sum(amounts.values()), 2), 100.0)

    def test_plan_buy_returns_shares_with_prices(self):
        code, out, err = self.run_cli("plan-buy", self.slug, "--amount", "100",
                                      "--prices", '{"NVDA": 200, "MSFT": 100}')
        self.assertEqual(code, 0, err)
        shares = dict((r["symbol"], r["shares"]) for r in out["orders"])
        self.assertAlmostEqual(shares["NVDA"], 0.3)
        self.assertAlmostEqual(shares["MSFT"], 0.4)

    def test_plan_buy_on_an_empty_basket_exits_one(self):
        self.run_cli("remove-holding", self.slug, "--symbol", "NVDA")
        self.run_cli("remove-holding", self.slug, "--symbol", "MSFT")
        code, _, err = self.run_cli("plan-buy", self.slug, "--amount", "100")
        self.assertEqual(code, 1)
        self.assertIn("EMPTY_BASKET", err)

    def test_plan_sell_all_returns_every_share(self):
        response = orders_response(
            order(order_id="b1", symbol="NVDA", quantity="10", average_price="50.00"))
        self.run_cli("record-fills", self.slug, "--orders-json", response,
                     "--order-ids", "b1", "--account", "000000000")
        code, out, err = self.run_cli("plan-sell", self.slug, "--all")
        self.assertEqual(code, 0, err)
        shares = dict((r["symbol"], r["shares"]) for r in out["orders"])
        self.assertEqual(shares["NVDA"], 10.0)

    def test_plan_sell_all_with_prices_returns_proceeds(self):
        response = orders_response(
            order(order_id="b1", symbol="NVDA", quantity="10", average_price="50.00"))
        self.run_cli("record-fills", self.slug, "--orders-json", response,
                     "--order-ids", "b1", "--account", "000000000")
        code, out, err = self.run_cli("plan-sell", self.slug, "--all",
                                      "--prices", '{"NVDA": 70}')
        self.assertEqual(code, 0, err)
        self.assertEqual(out["estimated_proceeds"], 700.0)

    def test_plan_sell_amount_keeps_the_current_weights(self):
        # NVDA 10 @ 50 = 500, MSFT 10 @ 25 = 250. Total 750.
        response = orders_response(
            order(order_id="b1", symbol="NVDA", quantity="10", average_price="50.00"),
            order(order_id="b2", symbol="MSFT", quantity="10", average_price="25.00"))
        self.run_cli("record-fills", self.slug, "--orders-json", response,
                     "--order-ids", "b1,b2", "--account", "000000000")
        code, out, err = self.run_cli("plan-sell", self.slug, "--amount", "75",
                                      "--prices", '{"NVDA": 50, "MSFT": 25}')
        self.assertEqual(code, 0, err)
        shares = dict((r["symbol"], r["shares"]) for r in out["orders"])
        # 10 percent of each holding's value.
        self.assertAlmostEqual(shares["NVDA"], 1.0)
        self.assertAlmostEqual(shares["MSFT"], 1.0)

    def test_plan_sell_above_the_basket_value_is_refused(self):
        response = orders_response(
            order(order_id="b1", symbol="NVDA", quantity="10", average_price="50.00"))
        self.run_cli("record-fills", self.slug, "--orders-json", response,
                     "--order-ids", "b1", "--account", "000000000")
        code, _, err = self.run_cli("plan-sell", self.slug, "--amount", "5000",
                                    "--prices", '{"NVDA": 50}')
        self.assertEqual(code, 1)
        self.assertIn("AMOUNT_ABOVE_VALUE", err)

    def test_plan_sell_amount_needs_prices(self):
        code, _, err = self.run_cli("plan-sell", self.slug, "--amount", "10")
        self.assertEqual(code, 1)
        self.assertIn("PRICES_REQUIRED", err)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.test_basket_cli.TestPlanning -v`
Expected: FAIL. `argparse` reports `invalid choice: 'plan-buy'`.

- [ ] **Step 3: Add the implementation**

Add to `basket.py`:

```python
def _require_holdings(basket):
    if not basket.holdings:
        raise CliError(
            "The basket %r holds no symbols. Add a holding first." % basket.slug,
            "EMPTY_BASKET", {"slug": basket.slug})


def cmd_plan_buy(args, store):
    _, basket = store.require(args.slug)
    _require_holdings(basket)
    prices = parse_prices(args.prices)

    orders = []
    for symbol, holding in basket.holdings.items():
        amount = args.amount * holding.target_weight_pct / 100.0
        row = {"symbol": symbol,
               "target_weight_pct": holding.target_weight_pct,
               "amount": round(amount, 2), "shares": None}
        price = prices.get(symbol)
        if price:
            row["shares"] = round(amount / price, 6)
            row["price"] = price
        orders.append(row)

    return {"slug": args.slug, "side": "buy", "amount": args.amount,
            "orders": orders}


def cmd_plan_sell(args, store):
    _, basket = store.require(args.slug)
    _require_holdings(basket)
    prices = parse_prices(args.prices)

    held = [(s, h) for s, h in basket.holdings.items() if h.has_position]
    if not held:
        raise CliError("The basket holds no shares", "NO_POSITION",
                       {"slug": args.slug})

    if args.all:
        orders = []
        proceeds = 0.0
        for symbol, holding in held:
            row = {"symbol": symbol, "shares": round(holding.position.shares, 6)}
            price = prices.get(symbol)
            if price:
                value = holding.position.shares * price
                row["price"] = price
                row["estimated_proceeds"] = round(value, 2)
                proceeds += value
            orders.append(row)
        return {"slug": args.slug, "side": "sell", "mode": "all",
                "orders": orders,
                "estimated_proceeds": round(proceeds, 2) if prices else None}

    if not prices:
        raise CliError("--amount needs --prices, because the split follows the "
                       "current market value", "PRICES_REQUIRED", {})

    values = {}
    total_value = 0.0
    for symbol, holding in held:
        price = prices.get(symbol)
        if not price:
            raise CliError("No price given for %s" % symbol, "PRICES_REQUIRED",
                           {"symbol": symbol})
        value = holding.position.shares * price
        values[symbol] = value
        total_value += value

    if args.amount > total_value + 1e-9:
        raise CliError(
            "The basket is worth %.2f, which is below the requested %.2f. "
            "Use --all to exit the basket." % (total_value, args.amount),
            "AMOUNT_ABOVE_VALUE",
            {"basket_value": round(total_value, 2), "requested": args.amount})

    fraction = args.amount / total_value
    orders = []
    for symbol, holding in held:
        shares = holding.position.shares * fraction
        orders.append({"symbol": symbol, "shares": round(shares, 6),
                       "price": prices[symbol],
                       "estimated_proceeds": round(shares * prices[symbol], 2)})
    return {"slug": args.slug, "side": "sell", "mode": "amount",
            "amount": args.amount, "basket_value": round(total_value, 2),
            "orders": orders}
```

Add the subparsers inside `build_parser`:

```python
    p = add("plan-buy", "Plan a whole-basket purchase")
    p.add_argument("slug")
    p.add_argument("--amount", type=float, required=True)
    p.add_argument("--prices", default="")
    p.set_defaults(func=cmd_plan_buy)

    p = add("plan-sell", "Plan a whole-basket sale")
    p.add_argument("slug")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--amount", type=float)
    group.add_argument("--all", action="store_true")
    p.add_argument("--prices", default="")
    p.set_defaults(func=cmd_plan_sell)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m unittest tests.test_basket_cli.TestPlanning -v`
Expected: PASS, 8 tests.

- [ ] **Step 5: Run the whole suite**

Run: `python3 -m unittest discover -s tests`
Expected: OK, 162 tests.

- [ ] **Step 6: Commit**

```bash
git add skills/basket-manager/scripts/basket.py tests/test_basket_cli.py
git commit -m "feat(basket): add plan-buy and plan-sell

plan-buy splits an amount by target weight. plan-sell --amount splits by
current market value, so a partial sale keeps the current weights. Both
refuse an empty basket."
```

---

## Task 8: verify and backup

**Files:**
- Modify: `skills/basket-manager/scripts/basket.py`
- Modify: `tests/test_basket_cli.py`

**Interfaces:**
- Consumes: `Store`, `CliError`, `EventLog`.
- Produces:
  - `positions_from_response(payload) -> dict` — maps symbol to share count.
  - Subcommands `verify` and `backup`.
  - `maybe_backup(store) -> bool` — runs a backup when the log is 20 events past the marker.
  - `BACKUP_EVERY = 20`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_basket_cli.py`:

```python
def positions_response(*pairs):
    rows = [{"symbol": s, "quantity": str(q), "average_buy_price": "50.00"}
            for s, q in pairs]
    return json.dumps({"data": {"positions": rows}})


class TestVerifyAndBackup(CliTestCase):

    def setUp(self):
        CliTestCase.setUp(self)
        self.slug = self.make_basket(name="Pair", symbols="NVDA:60,MSFT:40")
        response = orders_response(
            order(order_id="b1", symbol="NVDA", quantity="10", average_price="50.00"))
        self.run_cli("record-fills", self.slug, "--orders-json", response,
                     "--order-ids", "b1", "--account", "000000000")

    def test_claims_equal_position_is_correct(self):
        code, out, err = self.run_cli(
            "verify", "--positions", positions_response(("NVDA", 10)))
        self.assertEqual(code, 0, err)
        row = [r for r in out["positions"] if r["symbol"] == "NVDA"][0]
        self.assertEqual(row["state"], "match")

    def test_claims_below_position_is_normal(self):
        code, out, err = self.run_cli(
            "verify", "--positions", positions_response(("NVDA", 25)))
        self.assertEqual(code, 0, err)
        row = [r for r in out["positions"] if r["symbol"] == "NVDA"][0]
        self.assertEqual(row["state"], "outside_shares")
        self.assertEqual(out["warnings"], [])

    def test_claims_above_position_is_reported(self):
        code, out, err = self.run_cli(
            "verify", "--positions", positions_response(("NVDA", 4)))
        self.assertEqual(code, 0, err)
        row = [r for r in out["positions"] if r["symbol"] == "NVDA"][0]
        self.assertEqual(row["state"], "over_claimed")
        self.assertTrue(out["warnings"])

    def test_verify_without_positions_skips_the_third_check(self):
        code, out, err = self.run_cli("verify")
        self.assertEqual(code, 0, err)
        self.assertIsNone(out["positions"])

    def test_verify_reports_an_ignored_duplicate(self):
        log = self.data_dir / "events.log.jsonl"
        lines = log.read_text().splitlines()
        trade = [l for l in lines if '"type":"buy"' in l][0]
        with log.open("a") as handle:
            handle.write(trade + "\n")
        code, out, err = self.run_cli("verify")
        self.assertEqual(code, 0, err)
        self.assertEqual(len(out["ignored_events"]), 1)
        self.assertEqual(out["ignored_events"][0]["order_id"], "b1")

    def test_a_corrupt_line_exits_three(self):
        log = self.data_dir / "events.log.jsonl"
        with log.open("a") as handle:
            handle.write("{broken\n")
        code, _, err = self.run_cli("verify")
        self.assertEqual(code, 3)
        self.assertIn("CORRUPT_LOG_LINE", err)

    def test_backup_writes_a_copy_and_a_marker(self):
        code, out, err = self.run_cli("backup")
        self.assertEqual(code, 0, err)
        self.assertTrue(Path(out["path"]).exists())
        marker = json.loads((self.data_dir / "backup.marker").read_text())
        self.assertGreater(marker["events"], 0)

    def test_verify_warns_when_no_backup_exists(self):
        # setUp already wrote events, and the first write with no marker runs
        # a backup by itself. Remove the marker to reach the no-backup path.
        marker = self.data_dir / "backup.marker"
        if marker.exists():
            marker.unlink()
        out = self.run_cli("verify")[1]
        self.assertTrue(any("No backup marker" in w for w in out["warnings"]))

    def test_verify_warns_when_the_backup_is_stale(self):
        self.run_cli("backup")
        self.run_cli("set-weight", self.slug, "--symbol", "NVDA", "--weight", "70")
        out = self.run_cli("verify")[1]
        self.assertTrue(any("last backup held" in w for w in out["warnings"]))

    def test_verify_reports_a_clamped_oversell(self):
        # A hand-edited log can hold a sale larger than the basket ever held.
        log = self.data_dir / "events.log.jsonl"
        buy_line = [l for l in log.read_text().splitlines() if '"type":"buy"' in l][0]
        bad = buy_line.replace('"type":"buy"', '"type":"sell"')
        bad = bad.replace('"order_id":"b1"', '"order_id":"bad"')
        bad = bad.replace('"shares":10.0', '"shares":999.0')
        with log.open("a") as handle:
            handle.write(bad + "\n")
        out = self.run_cli("verify")[1]
        self.assertTrue(out["clamped_sells"])
        self.assertTrue(any("dropped" in w for w in out["warnings"]))

    def test_verify_accepts_a_slug(self):
        code, out, err = self.run_cli("verify", self.slug)
        self.assertEqual(code, 0, err)
        self.assertEqual(out["baskets"], [self.slug])

    def test_backup_honours_the_to_option(self):
        target = self.data_dir / "elsewhere"
        code, out, err = self.run_cli("backup", "--to", str(target))
        self.assertEqual(code, 0, err)
        self.assertTrue(Path(out["path"]).parent == target)

    def test_verify_stops_warning_after_a_backup(self):
        self.run_cli("backup")
        out = self.run_cli("verify")[1]
        self.assertFalse(any("backup" in w.lower() for w in out["warnings"]))

    def test_the_tool_backs_up_by_itself(self):
        # The setUp already wrote several events. Drive the count past the
        # threshold and confirm a backup appeared without an explicit call.
        for index in range(12):
            self.run_cli("set-weight", self.slug, "--symbol", "NVDA",
                         "--weight", str(50 + (index % 5)))
        backups = list((self.data_dir / "backups").glob("*.jsonl"))
        self.assertTrue(backups)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.test_basket_cli.TestVerifyAndBackup -v`
Expected: FAIL. `argparse` reports `invalid choice: 'verify'`.

- [ ] **Step 3: Add the implementation**

Add to `basket.py`:

```python
BACKUP_EVERY = 20
MARKER_NAME = "backup.marker"
BACKUP_DIR_NAME = "backups"


def positions_from_response(payload):
    """Return {symbol: shares} from a get_equity_positions response."""
    if not payload:
        return None
    try:
        data = json.loads(payload)
    except ValueError as error:
        raise CliError("The --positions value is not valid JSON: %s" % error,
                       "BAD_POSITIONS_JSON", {})
    if isinstance(data, dict) and "data" in data:
        data = data["data"]
    if isinstance(data, dict):
        for key in ("positions", "results"):
            if key in data:
                data = data[key]
                break
    if not isinstance(data, list):
        raise CliError("Expected a list of positions", "BAD_POSITIONS_JSON", {})

    out = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        symbol = (entry.get("symbol") or "").upper()
        if not symbol:
            continue
        try:
            out[symbol] = float(entry.get("quantity") or 0)
        except (TypeError, ValueError):
            continue
    return out


def read_marker(store):
    path = store.data_dir / MARKER_NAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except ValueError:
        return None


def run_backup(store, to=None):
    """Copy the log to a timestamped file and update the marker."""
    if not store.log.path.exists():
        return None
    backups = Path(to) if to else store.data_dir / BACKUP_DIR_NAME
    backups.mkdir(parents=True, exist_ok=True)
    with store.log.locked():
        count = store.log.count()
        stamp = utc_now().replace(":", "").replace("-", "").replace(".", "")
        target = backups / ("events-%s.jsonl" % stamp)
        shutil.copyfile(str(store.log.path), str(target))
    (store.data_dir / MARKER_NAME).write_text(json.dumps({
        "events": count, "at": utc_now(), "path": str(target)}) + "\n")
    return target


def maybe_backup(store):
    """Back the log up when it has grown past the threshold."""
    marker = read_marker(store)
    count = store.log.count()
    if marker is None or count - marker.get("events", 0) >= BACKUP_EVERY:
        run_backup(store)
        return True
    return False


def cmd_backup(args, store):
    target = run_backup(store, args.to)
    if target is None:
        raise CliError("There is no log to back up", "NO_LOG", {})
    return {"path": str(target), "events": store.log.count()}


def cmd_verify(args, store):
    result = store.load()          # raises CliError(exit 3) on a corrupt line
    warnings = []

    if args.slug and args.slug not in result.baskets:
        raise CliError("No basket has the slug %r" % args.slug,
                       "BASKET_NOT_FOUND",
                       {"slug": args.slug, "available": sorted(result.baskets)})

    def wanted(slug):
        return args.slug is None or slug == args.slug

    ignored = []
    for entry in result.ignored:
        if not wanted(entry["slug"]):
            continue
        ignored.append(entry)
        if entry["slug"] != entry["kept_by"]:
            warnings.append(
                "Line %s repeats order %s. Basket %s kept it, so basket %s "
                "lost that claim." % (entry["line"], entry["order_id"],
                                      entry["kept_by"], entry["slug"]))

    clamped = [c for c in result.clamped if wanted(c["slug"])]
    for entry in clamped:
        warnings.append(
            "Line %s sells %s shares of %s, but basket %s held fewer. The "
            "replay covered what it could and dropped %s shares."
            % (entry["line"], entry["requested"], entry["symbol"],
               entry["slug"], entry["oversold"]))

    marker = read_marker(store)
    count = store.log.count()
    if marker is None:
        warnings.append("No backup marker exists. Run `backup`.")
    elif count > marker.get("events", 0):
        warnings.append(
            "The log holds %d events, but the last backup held %d. Run `backup`."
            % (count, marker.get("events", 0)))

    positions = positions_from_response(args.positions)
    rows = None
    if positions is not None:
        claims = {}
        for slug, basket in result.baskets.items():
            if not wanted(slug):
                continue
            for symbol, holding in basket.holdings.items():
                if holding.has_position:
                    claims[symbol] = claims.get(symbol, 0.0) + holding.position.shares

        rows = []
        for symbol in sorted(set(claims) | set(positions)):
            claimed = claims.get(symbol, 0.0)
            actual = positions.get(symbol, 0.0)
            if abs(claimed - actual) < 1e-6:
                state = "match"
            elif claimed < actual:
                state = "outside_shares"
            else:
                state = "over_claimed"
                warnings.append(
                    "Baskets claim %s shares of %s, but the account holds %s. "
                    "Section 7.4 of the design gives the repair."
                    % (claimed, symbol, actual))
            rows.append({"symbol": symbol, "claimed": round(claimed, 6),
                         "account": round(actual, 6), "state": state})

    return {"baskets": sorted(s for s in result.baskets if wanted(s)),
            "events": count, "ignored_events": ignored,
            "clamped_sells": clamped, "positions": rows, "warnings": warnings}
```

Call `maybe_backup` from `Store.write`, after the export:

```python
    def write(self, events, slugs):
        """Append events, then export the snapshots for the changed baskets."""
        self.log.append(events)
        self.export(slugs)
        maybe_backup(self)
```

Add the subparsers inside `build_parser`:

```python
    p = add("verify", "Check the log against the account")
    p.add_argument("slug", nargs="?", default=None)
    p.add_argument("--positions", default="")
    p.set_defaults(func=cmd_verify)

    p = add("backup", "Copy the event log")
    p.add_argument("--to", default=None)
    p.set_defaults(func=cmd_backup)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m unittest tests.test_basket_cli.TestVerifyAndBackup -v`
Expected: PASS, 15 tests.

- [ ] **Step 5: Run the whole suite**

Run: `python3 -m unittest discover -s tests`
Expected: OK, 177 tests.

- [ ] **Step 6: Commit**

```bash
git add skills/basket-manager/scripts/basket.py tests/test_basket_cli.py
git commit -m "feat(basket): add verify and backup

verify replays the log, reports every ignored duplicate, warns on a
stale backup, and compares basket claims to the real account position.
The tool backs the log up by itself every 20 events."
```

---

## Task 9: Stage 1 acceptance

**Files:**
- Create: `tests/test_basket_acceptance.py`

**Interfaces:**
- Consumes: the `basket.py` CLI.
- Produces: nothing that later tasks use.

- [ ] **Step 1: Write the failing test**

Create `tests/test_basket_acceptance.py`:

```python
#!/usr/bin/env python3
"""End-to-end walk through the Stage 1 journeys.

This test follows the data flow in section 7 of the design document with the
live Magnificent 7 figures, so a change that breaks a journey fails here even
when every unit test still passes.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "skills" / "basket-manager" / "scripts" / "basket.py"

MAG7 = ["NVDA", "MSFT", "AAPL", "GOOGL", "AMZN", "META", "SPCX"]

FILLS = {
    "NVDA": ("6a626aa2", 0.068659, 208.1299),
    "MSFT": ("6a626aa4", 0.037476, 381.3099),
    "AAPL": ("6a626aa6", 0.044506, 321.0799),
    "GOOGL": ("6a626aa8", 0.044930, 318.0499),
    "AMZN": ("6a626aaa", 0.061141, 233.5579),
    "META": ("6a626aac", 0.023513, 607.3099),
    "SPCX": ("6a626aae", 0.122470, 116.5999),
}


class TestStageOneAcceptance(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *args):
        proc = subprocess.run(
            [sys.executable, str(CLI), "--data-dir", str(self.data_dir)] + list(args),
            capture_output=True, text=True)
        payload = json.loads(proc.stdout) if proc.stdout.strip() else None
        return proc.returncode, payload, proc.stderr

    def test_create_buy_and_report(self):
        symbols = ",".join("%s:1" % s for s in MAG7)
        code, out, err = self.cli("create", "Magnificent 7 Index",
                                  "--symbols", symbols, "--account", "000000000")
        self.assertEqual(code, 0, err)
        slug = out["slug"]

        weights = dict((h["symbol"], h["target_weight_pct"]) for h in out["holdings"])
        self.assertEqual(sum(weights.values()), 100)
        self.assertEqual(weights["AAPL"], 15)
        self.assertEqual(weights["AMZN"], 15)

        code, plan, err = self.cli("plan-buy", slug, "--amount", "100")
        self.assertEqual(code, 0, err)
        self.assertEqual(round(sum(r["amount"] for r in plan["orders"]), 2), 100.0)

        orders = []
        for symbol, (order_id, shares, price) in FILLS.items():
            orders.append({
                "id": order_id, "symbol": symbol, "side": "buy", "state": "filled",
                "quantity": str(shares), "cumulative_quantity": str(shares),
                "price": str(round(price - 1.2, 4)), "average_price": str(price),
                "last_transaction_at": "2026-07-23T19:25:23.115Z",
                "executions": [{"price": str(price), "quantity": str(shares)}],
            })
        response = json.dumps({"data": {"orders": orders}})
        ids = ",".join(o["id"] for o in orders)

        code, out, err = self.cli("record-fills", slug, "--orders-json", response,
                                  "--order-ids", ids, "--account", "000000000")
        self.assertEqual(code, 0, err)
        self.assertEqual(len(out["recorded"]), 7)

        code, shown, err = self.cli("show", slug)
        self.assertEqual(code, 0, err)
        expected = sum(s * p for _, s, p in FILLS.values())
        self.assertAlmostEqual(shown["totals"]["total_invested"],
                               round(expected, 2), places=2)

        nvda = [h for h in shown["holdings"] if h["symbol"] == "NVDA"][0]
        self.assertAlmostEqual(nvda["position"]["avg_cost"], 208.1299, places=4)

    def test_a_second_record_of_the_same_orders_changes_nothing(self):
        symbols = ",".join("%s:1" % s for s in MAG7)
        slug = self.cli("create", "M7", "--symbols", symbols,
                        "--account", "000000000")[1]["slug"]
        order_id, shares, price = FILLS["NVDA"]
        response = json.dumps({"data": {"orders": [{
            "id": order_id, "symbol": "NVDA", "side": "buy", "state": "filled",
            "quantity": str(shares), "cumulative_quantity": str(shares),
            "price": "206.80", "average_price": str(price),
            "last_transaction_at": "2026-07-23T19:25:23.115Z",
            "executions": [{"price": str(price), "quantity": str(shares)}]}]}})

        self.cli("record-fills", slug, "--orders-json", response,
                 "--order-ids", order_id, "--account", "000000000")
        first = self.cli("show", slug)[1]
        self.cli("record-fills", slug, "--orders-json", response,
                 "--order-ids", order_id, "--account", "000000000")
        second = self.cli("show", slug)[1]

        del first["totals"]["built_at"], second["totals"]["built_at"]
        del first["updated_at"], second["updated_at"]
        self.assertEqual(first, second)

    def test_the_log_survives_losing_every_snapshot(self):
        slug = self.cli("create", "M7", "--symbols", "NVDA:1,MSFT:1",
                        "--account", "000000000")[1]["slug"]
        before = self.cli("show", slug)[1]
        for path in (self.data_dir / "baskets").glob("*.json"):
            path.unlink()
        after = self.cli("show", slug)[1]
        del before["totals"]["built_at"], after["totals"]["built_at"]
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test**

Run: `python3 -m unittest tests.test_basket_acceptance -v`
Expected: PASS, 3 tests. If any fail, the failure is in an earlier task; fix it there and re-run that task's tests too.

- [ ] **Step 3: Run the whole suite on the oldest supported Python**

Run: `python3 -m unittest discover -s tests`
Expected: OK, 180 tests.

If a 3.9 interpreter is available, also run:
`python3.9 -m unittest discover -s tests`
Expected: OK. A failure here means a 3.10+ syntax slipped in; check the Global Constraints list.

- [ ] **Step 4: Check the CI workflow still passes its own checks**

Run: `python3 -c "import json,pathlib; [json.loads(pathlib.Path(p).read_text()) for p in ['.claude-plugin/plugin.json','.codex-plugin/plugin.json','.cursor-plugin/plugin.json','config.json']]" && echo ok`
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add tests/test_basket_acceptance.py
git commit -m "test(basket): add the Stage 1 acceptance walk

Follows the section 7 journeys with the live Magnificent 7 figures:
create, plan, record seven fills, and report. Also pins idempotency and
snapshot disposability."
```

---

## Stage 2 outline: the migration

Stage 2 is a separate plan. Write it after Stage 1 passes, because the
migration calls the Stage 1 modules and the plan should quote their final
signatures.

Its shape, from §8 of the design:

1. `migrate_v2.py` reads three sources: the `Z64:` metadata from
   `get_watchlists`, the legacy `data/baskets/*.json` files for the thesis
   text, and `get_equity_orders` for the trades.
2. It attributes orders to baskets with the algorithm in §8.2, using a
   tolerance of 0.000005 shares, and reports every unresolved order.
3. It normalizes the target weights, so Magnificent 7 becomes 15/15/14/14/14/14/14.
4. It writes through `basket_events.py`, never directly to the log (§8.4).
5. `--dry-run` is the default; `--apply` writes.
6. It changes no watchlist.
7. Tests reuse the captured MCP payloads already in `tests/test_basket_utils.py`.

## Stage 3 outline: the skills and documents

1. Rewrite `skills/basket-manager/SKILL.md` for the local store and every
   command.
2. Rewrite the "Recording Basket Transactions" section of
   `skills/trade-executor/SKILL.md` around `record-fills` with an order id.
3. Point `skills/portfolio-tracker/SKILL.md` at `basket.py show` and check 3
   of `verify`.
4. Adjust the cross-skill offers in `stock-researcher` and `stock-screener`.
5. Update `AGENTS.md` and `README.md` (§11.3).
6. Fix the script paths to use `${CLAUDE_PLUGIN_ROOT}` (§11.4).
7. Delete `basket_utils.py`, `basket_summary.py`, `list_symbols.py`, and
   `migrate_to_watchlists.py`. Retarget `calc_performance.py` and
   `calc_drift.py` at the local store.
