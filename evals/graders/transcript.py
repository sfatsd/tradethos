"""Read what an agent did, so assertions can ask about actions not words.

Most rules in these skills are about sequence. Review before place. Check
tradability before ordering. Verify before reporting a basket. A final
message can claim any of that, and on 2026-08-03 a final message claimed
something that was not true. The transcript is the record that does not
depend on the agent's account of itself.

`precedes` carries the subtlety worth explaining. "The review runs before the
order" is not one global ordering question, it is one question per symbol. A
batch that reviews NVDA, then places NVDA, then places MSFT without reviewing
it has a review before a place in the global sequence, and is still wrong.
Matching on a key answers the question that was actually asked.
"""

import json
import os


class Transcript(object):
    """An ordered list of tool calls with a few questions it can answer."""

    def __init__(self, calls=None):
        self.calls = list(calls or [])

    @classmethod
    def from_file(cls, path):
        if not path or not os.path.exists(path):
            return cls([])
        with open(path) as handle:
            return cls([json.loads(line) for line in handle if line.strip()])

    def __len__(self):
        return len(self.calls)

    def tools(self):
        return [c.get("tool") for c in self.calls]

    def calls_to(self, tool):
        return [c for c in self.calls if c.get("tool") == tool]

    def called(self, tool):
        return bool(self.calls_to(tool))

    def arguments(self, tool, key):
        """Every value a tool was given for one argument, in call order."""
        return [(c.get("arguments") or {}).get(key) for c in self.calls_to(tool)]

    def index_of(self, tool):
        for position, call in enumerate(self.calls):
            if call.get("tool") == tool:
                return position
        return None

    def precedes(self, before, after, key=None):
        """Return the `after` calls that no matching `before` call preceded.

        With a key, an `after` call is satisfied only by an earlier `before`
        call that carried the same value for that key. Without one, any
        earlier `before` call satisfies it.
        """
        unmatched = []
        seen = set()
        seen_any = False
        for call in self.calls:
            tool = call.get("tool")
            arguments = call.get("arguments") or {}
            if tool == before:
                seen_any = True
                if key is not None:
                    seen.add(arguments.get(key))
            if tool == after:
                if key is None:
                    if not seen_any:
                        unmatched.append(call)
                elif arguments.get(key) not in seen:
                    unmatched.append(call)
        return unmatched


class RunArtifacts(object):
    """Everything one eval run produced, in one place.

    A grader should not need to know where any of this came from, so the
    loading lives here and the assertions take this object.
    """

    def __init__(self, transcript=None, events=None, final_message="",
                 orders_by_id=None, expected_order_ids=None, slug=None,
                 broker=None):
        self.transcript = transcript or Transcript()
        self.events = events or []
        self.final_message = final_message or ""
        self.orders_by_id = orders_by_id or {}
        self.expected_order_ids = list(expected_order_ids or [])
        self.slug = slug
        self.broker = broker

    @classmethod
    def from_directory(cls, path, slug=None, expected_order_ids=None):
        path = str(path).rstrip("/")

        def maybe(name):
            full = os.path.join(path, name)
            return full if os.path.exists(full) else None

        events = []
        log = maybe("events.log.jsonl")
        if log:
            with open(log) as handle:
                events = [json.loads(line) for line in handle if line.strip()]

        final = ""
        message = maybe("final_message.txt")
        if message:
            with open(message) as handle:
                final = handle.read()

        orders_by_id = {}
        orders = maybe("orders.json")
        if orders:
            with open(orders) as handle:
                payload = json.load(handle)
            rows = (payload.get("data") or {}).get("orders") or []
            orders_by_id = dict((o["id"], o) for o in rows if o.get("id"))

        return cls(
            transcript=Transcript.from_file(maybe("transcript.jsonl")),
            events=events, final_message=final, orders_by_id=orders_by_id,
            expected_order_ids=expected_order_ids
                or sorted(orders_by_id.keys()),
            slug=slug)

    def trades(self):
        return [e for e in self.events
                if e.get("type") in ("buy", "sell")
                and (self.slug is None or e.get("slug") == self.slug)]
