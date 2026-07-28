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
