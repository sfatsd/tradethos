#!/usr/bin/env python3
"""The append-only event log.

The log is the source of truth for every basket. This module owns all input
and output for that file, and it owns the lock that makes a write safe.
"""

import datetime
import json
import os
import re
from contextlib import contextmanager

try:
    import fcntl
except ImportError:                                   # pragma: no cover - Windows
    fcntl = None

# True when this platform gives us advisory file locks. Windows has no fcntl,
# and the tool ships as a plugin to editors that run there, so an ImportError
# at module scope would take the whole CLI down. Without fcntl the tool still
# reads and writes; it simply has no protection against a concurrent writer,
# and `verify` says so out loud. Tests flip this flag to reach that path.
LOCKING_AVAILABLE = fcntl is not None

EVENT_VERSION = 1
LOG_NAME = "events.log.jsonl"


class EventLogError(Exception):
    """The log cannot be read or written."""


class CorruptLineError(EventLogError):
    """A line in the log is not valid JSON.

    The normal read path no longer raises this: it collects corrupt lines and
    keeps going, so one bad line cannot hide every basket. The type stays for
    a caller that wants to raise on corruption itself.
    """

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


EPOCH = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)

_TS_SHAPE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(\.\d+)?(Z|z|[+-]\d{2}:?\d{2})?$")


def parse_timestamp(text):
    """Return an aware datetime for an ISO 8601 string, or None.

    The brokerage sends a fill time with anywhere from two to six fractional
    second digits and a `Z` suffix. Python 3.9's `fromisoformat` accepts
    neither the `Z` nor a fraction that is not exactly three or six digits, so
    the string is normalized first. A string this cannot read returns None,
    and the caller must treat that as "no usable time" rather than as a zero.
    """
    if not isinstance(text, str):
        return None
    match = _TS_SHAPE.match(text.strip())
    if match is None:
        return None
    date, clock, fraction, zone = match.groups()

    # Truncate or pad the fraction to the six digits fromisoformat wants.
    fraction = "." + fraction[1:7].ljust(6, "0") if fraction else ""
    if zone in (None, "", "Z", "z"):
        zone = "+00:00"
    elif ":" not in zone:
        zone = zone[:3] + ":" + zone[3:]

    try:
        return datetime.datetime.fromisoformat(
            "%sT%s%s%s" % (date, clock, fraction, zone))
    except ValueError:
        return None


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

        On a platform with no fcntl the block still runs, unlocked. That is
        weaker, and `verify` reports it; refusing to run at all would leave
        the whole tool unusable there.
        """
        self._ensure_parent()
        if self._depth == 0:
            self._handle = self.path.open("a+")
            if LOCKING_AVAILABLE:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX)
        self._depth += 1
        try:
            yield
        finally:
            self._depth -= 1
            if self._depth == 0:
                if LOCKING_AVAILABLE:
                    fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
                self._handle.close()
                self._handle = None

    def _parse(self, handle):
        """Return (events, corrupt) for one open handle.

        A line that is not valid JSON is collected, not raised. A crash in the
        middle of a flush leaves exactly one truncated line, and that line must
        not make every other basket in the store unreadable.
        """
        events = []
        corrupt = []
        for number, raw in enumerate(handle, start=1):
            text = raw.strip()
            if not text:
                continue
            try:
                event = json.loads(text)
            except ValueError:
                corrupt.append({"line": number, "raw": text[:120]})
                continue
            if not isinstance(event, dict):
                corrupt.append({"line": number, "raw": text[:120]})
                continue
            event["_line"] = number
            events.append(event)
        return events, corrupt

    def read_with_corruption(self):
        """Return (events, corrupt_lines) in log order.

        Each event carries a `_line` key that holds its 1-based line number.
        Each corrupt entry holds `line` and a `raw` excerpt. The read never
        raises on a bad line: the replay skips it, which degrades only the
        basket whose events are incomplete.

        A read takes a shared lock, so it never sees a half-written last line
        while another session appends. When this object already holds the
        exclusive lock, it must not ask for a second lock: flock ties a lock
        to the open file description, so a new descriptor in the same process
        would block against our own exclusive lock and deadlock.
        """
        if not self.path.exists():
            return [], []

        if self._depth > 0:
            with self.path.open("r") as handle:
                return self._parse(handle)

        with self.path.open("r") as handle:
            if LOCKING_AVAILABLE:
                fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            try:
                return self._parse(handle)
            finally:
                if LOCKING_AVAILABLE:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def read(self):
        """Return every readable event in log order, skipping corrupt lines."""
        return self.read_with_corruption()[0]

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
