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
