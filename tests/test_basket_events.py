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

import basket_events
from basket_events import (
    EVENT_VERSION,
    CorruptLineError,
    EventLog,
    make_event,
    parse_timestamp,
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

    def test_count_agrees_with_read_when_a_line_is_torn(self):
        # count() goes through the same locked, corruption-aware read as
        # everything else, so a torn line is not counted as an event and the
        # count can never disagree with what a replay finds.
        self.log.append([make_event("basket_created", "a", name="A")])
        with self.log.path.open("a") as handle:
            handle.write('{"type": "holding_add')
        self.assertEqual(self.log.count(), 1)
        self.assertEqual(self.log.count(), len(self.log.read()))

    def test_each_line_is_valid_json_on_its_own(self):
        self.log.append([make_event("basket_created", "a", name="A")])
        raw = self.log.path.read_text().splitlines()
        self.assertEqual(len(raw), 1)
        json.loads(raw[0])

    def test_a_corrupt_line_is_reported_not_raised(self):
        # A crash mid-flush leaves one truncated line. That line must not make
        # the rest of the log unreadable.
        self.log.append([make_event("basket_created", "a", name="A")])
        with self.log.path.open("a") as handle:
            handle.write("{not json\n")
        self.log.append([make_event("basket_created", "b", name="B")])

        events, corrupt = self.log.read_with_corruption()
        self.assertEqual([e["slug"] for e in events], ["a", "b"])
        self.assertEqual(len(corrupt), 1)
        self.assertEqual(corrupt[0]["line"], 2)
        self.assertIn("{not json", corrupt[0]["raw"])

    def test_read_skips_a_corrupt_line(self):
        self.log.append([make_event("basket_created", "a", name="A")])
        with self.log.path.open("a") as handle:
            handle.write("truncated{\n")
        self.assertEqual([e["slug"] for e in self.log.read()], ["a"])

    def test_a_json_line_that_is_not_an_object_is_corrupt(self):
        with self.log.path.open("a") as handle:
            handle.write("[1, 2, 3]\n")
        events, corrupt = self.log.read_with_corruption()
        self.assertEqual(events, [])
        self.assertEqual(corrupt[0]["line"], 1)

    def test_a_blank_line_is_skipped(self):
        self.log.append([make_event("basket_created", "a", name="A")])
        with self.log.path.open("a") as handle:
            handle.write("\n")
        self.assertEqual(len(self.log.read()), 1)

    def test_locked_is_reentrant_for_one_process(self):
        with self.log.locked():
            self.log.append([make_event("basket_created", "a", name="A")])
        self.assertEqual(self.log.count(), 1)


class TestParseTimestamp(unittest.TestCase):
    """The brokerage sends two to six fractional digits and a Z suffix.

    Python 3.9's fromisoformat accepts neither, so the raw strings cannot be
    handed to it, and they cannot be compared as text either.
    """

    def test_three_fractional_digits(self):
        stamp = parse_timestamp("2026-07-23T19:25:23.115Z")
        self.assertEqual(stamp.year, 2026)
        self.assertEqual(stamp.microsecond, 115000)

    def test_six_fractional_digits(self):
        stamp = parse_timestamp("2026-07-23T19:25:22.952062Z")
        self.assertEqual(stamp.microsecond, 952062)

    def test_two_fractional_digits(self):
        self.assertEqual(parse_timestamp("2026-01-01T15:00:00.12Z").microsecond,
                         120000)

    def test_no_fractional_digits(self):
        self.assertEqual(parse_timestamp("2026-01-03T15:00:00Z").microsecond, 0)

    def test_more_than_six_fractional_digits_truncates(self):
        self.assertEqual(
            parse_timestamp("2026-01-03T15:00:00.1234567Z").microsecond, 123456)

    def test_an_explicit_offset_is_honoured(self):
        # 15:00 at -05:00 is 20:00 UTC, which is after 19:00 UTC.
        early = parse_timestamp("2026-01-03T15:00:00-05:00")
        late = parse_timestamp("2026-01-03T19:00:00Z")
        self.assertGreater(early, late)

    def test_every_result_is_comparable(self):
        # Mixed shapes must order correctly, which raw string compare fails:
        # "2026-01-01T15:00:00.12Z" > "2026-01-01T15:00:00Z" as text.
        stamps = [parse_timestamp(t) for t in
                  ("2026-01-01T15:00:00.999999Z", "2026-01-01T15:00:00Z")]
        self.assertGreater(stamps[0], stamps[1])

    def test_a_string_it_cannot_read_gives_none(self):
        for bad in ("", "not a date", "2026-13-45T99:99:99Z", "1753900000"):
            self.assertIsNone(parse_timestamp(bad), bad)

    def test_a_non_string_gives_none(self):
        for bad in (None, 17539, {}, []):
            self.assertIsNone(parse_timestamp(bad))


class TestLockingFallback(unittest.TestCase):
    """Windows has no fcntl. The tool must still run there.

    `import fcntl` at module scope was a hard ImportError, which took the
    whole CLI down on a platform the plugin ships to. The fallback drops the
    lock, and that has to be visible rather than silent.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = EventLog(Path(self.tmp.name))
        self.saved = basket_events.LOCKING_AVAILABLE
        basket_events.LOCKING_AVAILABLE = False

    def tearDown(self):
        basket_events.LOCKING_AVAILABLE = self.saved
        self.tmp.cleanup()

    def test_the_flag_is_true_on_this_platform(self):
        # POSIX behaviour is unchanged: the real locks are still taken.
        self.assertTrue(self.saved)

    def test_append_and_read_still_work_without_locking(self):
        self.log.append([make_event("basket_created", "a", name="A")])
        self.log.append([make_event("holding_added", "a", symbol="NVDA")])
        events = self.log.read()
        self.assertEqual([e["type"] for e in events],
                         ["basket_created", "holding_added"])

    def test_the_locked_block_still_runs_without_locking(self):
        with self.log.locked():
            self.log.append([make_event("basket_created", "a", name="A")])
        self.assertEqual(self.log.count(), 1)

    def test_a_corrupt_line_still_degrades_without_locking(self):
        self.log.append([make_event("basket_created", "a", name="A")])
        with self.log.path.open("a") as handle:
            handle.write("{broken\n")
        events, corrupt = self.log.read_with_corruption()
        self.assertEqual(len(events), 1)
        self.assertEqual(corrupt[0]["line"], 2)


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
