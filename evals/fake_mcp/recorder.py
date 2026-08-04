"""An MCP server that records requests and never decides anything.

Every defect this harness produced was a simulation defect. The fake refused
market orders after hours, which is a rule the real broker does not have. A
sell added shares and took cash. A dollar-amount order sized itself off the
quote and filled at the ask, so the notional was out by the spread. `run_scan`
ignored its arguments. Each one made a correct agent look wrong, which is the
most expensive kind of wrong result: it sends someone to fix a skill that was
never broken.

None of those bugs were in the assertions. They were in the part that tried
to be a brokerage.

So this does not try. It reads canned responses from a file, hands them back,
and appends every request to a log. The assertions then ask what the agent
asked for - which is what the skills actually govern. "Was the review called
before the order, for that symbol" and "did the draft carry the right account"
are questions about a request. They never needed a simulated fill.

**Responses still have to exist.** An agent that calls `get_accounts` and gets
nothing cannot choose an account, so it stalls, and the run measures a broken
conversation instead of a skill. Canned is not the same as absent: the
responses are fixed, realistic, and dumb.

A tool may map to one response, reused for every call, or to a list consumed
in order. The list is how a case scripts a sequence - a transport failure
followed by a success, for instance - without anything here deciding when to
fail.

    python -m evals.fake_mcp.recorder \\
        --responses case.json --requests requests.jsonl
"""

import argparse
import json
import sys

from .server import TOOLS, TOOL_NAMES, PROTOCOL_VERSION


def echo_identity(name, arguments, payload, quotes=None):
    """Stop a canned response contradicting the request that asked for it.

    There is a line here worth keeping sharp.

    *Deciding an outcome* - whether an order fills or queues, at what price,
    whether there is buying power - is simulation, and simulation is what
    kept being wrong. None of that happens here.

    *Not contradicting the request* is a different thing. A review that comes
    back for WDC when the agent asked about NVDA is not a simplification, it
    is a lie, and an agent that notices is right to refuse. That is exactly
    what happened: a run stopped before placing and reported the review tool
    as returning the wrong symbol. The agent behaved perfectly and the case
    failed, which means the fixture was the fault.

    So the identity fields are echoed and nothing else is. No arithmetic, no
    rules, no state.
    """
    if not isinstance(payload, dict) or "data" not in payload:
        return payload
    data = payload["data"]

    if name == "review_equity_order":
        for field in ("symbol", "side", "type", "dollar_amount", "quantity",
                      "limit_price", "market_hours", "account_number"):
            if field in arguments:
                data[field] = arguments[field]
        # The embedded quote has to follow the symbol. Echoing one and not
        # the other is worse than echoing neither: a response that is
        # uniformly about the wrong stock is at least self-consistent, while
        # one whose header says NVDA and whose quote says WDC is a fresh
        # contradiction that this function introduced. A run caught exactly
        # that and refused to place, which was the right call.
        quote = quotes.get(arguments.get("symbol")) if quotes else None
        if quote is not None:
            data["quote_data"] = quote

    elif name in ("get_equity_quotes", "get_equity_fundamentals",
                  "get_equity_historicals",
                  "get_equity_technical_indicators"):
        wanted = arguments.get("symbols")
        if wanted:
            wanted = set(wanted)
            key = "results"
            rows = data.get(key) or []
            kept = [r for r in rows
                    if (r.get("quote") or r).get("symbol") in wanted]
            # Asking for a symbol the fixture does not carry should read as
            # "no data", not as "here is a different symbol".
            data[key] = kept

    elif name == "get_equity_tradability":
        wanted = arguments.get("symbols")
        if wanted:
            wanted = set(wanted)
            data["results"] = [r for r in data.get("results") or []
                               if r.get("symbol") in wanted]

    elif name == "place_equity_order":
        order = data.get("order")
        if isinstance(order, dict):
            for field in ("symbol", "side", "type", "market_hours"):
                if field in arguments:
                    order[field] = arguments[field]
    return payload


class Recorder(object):

    def __init__(self, responses=None, requests_path=None):
        self.responses = responses or {}
        self.requests_path = requests_path
        self.consumed = {}

    def quote_index(self):
        """Map symbol to its pre-written quote, for nested echoing.

        This selects a row that was written before the run. It does not
        compute one, so it stays on the fixture side of the line.
        """
        canned = self.responses.get("get_equity_quotes") or {}
        rows = ((canned.get("data") or {}).get("results")
                if isinstance(canned, dict) else None) or []
        index = {}
        for row in rows:
            quote = row.get("quote") or row
            if quote.get("symbol"):
                index[quote["symbol"]] = quote
        return index

    def _log(self, entry):
        if not self.requests_path:
            return
        # Appended and flushed per call. A run that dies half way is the run
        # whose requests matter most.
        with open(self.requests_path, "a") as handle:
            handle.write(json.dumps(entry) + "\n")

    def response_for(self, name):
        """Return the next canned response for a tool.

        A list is consumed in order and the last entry repeats, so a case
        that scripts two answers does not run dry if the agent calls a third
        time. Nothing here inspects the arguments: a response that varied
        with the request would be a simulation again, wearing a smaller hat.
        """
        canned = self.responses.get(name)
        if canned is None:
            return {"error": "No canned response for %s. Add one to the "
                             "case's responses file." % name}
        if isinstance(canned, list):
            if not canned:
                return {"error": "Empty response list for %s" % name}
            index = min(self.consumed.get(name, 0), len(canned) - 1)
            self.consumed[name] = index + 1
            return canned[index]
        return canned

    def call_tool(self, name, arguments):
        entry = {"tool": name, "arguments": arguments}
        if name not in TOOL_NAMES:
            entry["unknown_tool"] = True
            self._log(entry)
            return {"error": "Unknown tool %s" % name}, True
        payload = echo_identity(name, arguments,
                                self.response_for(name),
                                self.quote_index())
        # The response goes into the log beside the request. An assertion
        # about what the agent did with an answer needs the answer, and the
        # log is the only record either way.
        entry["response"] = payload
        self._log(entry)
        return payload, isinstance(payload, dict) and "error" in payload

    def handle(self, request):
        method = request.get("method")
        request_id = request.get("id")

        if method == "initialize":
            return self._ok(request_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "tradethos-recorder",
                               "version": "0.1.0"}})
        if method == "tools/list":
            return self._ok(request_id, {"tools": TOOLS})
        if method == "tools/call":
            params = request.get("params") or {}
            payload, is_error = self.call_tool(params.get("name"),
                                               params.get("arguments") or {})
            # A transport failure and a tool error are different events, and
            # only one of them is worth retrying. The first version returned
            # both as tool results, so a scripted "upstream timeout" read as
            # "the broker answered, and the answer was no" - the agent
            # sensibly did not retry, and the case that tests retry
            # behaviour could never pass.
            if isinstance(payload, dict) and payload.get("transport"):
                return self._error(request_id, -32000,
                                   payload.get("error", "transport failure"))
            result = {"content": [{"type": "text",
                                   "text": json.dumps(payload)}]}
            if is_error:
                result["isError"] = True
            return self._ok(request_id, result)
        if method is not None and request_id is None:
            return None
        return self._error(request_id, -32601, "Unknown method %s" % method)

    @staticmethod
    def _ok(request_id, result):
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id, code, message):
        return {"jsonrpc": "2.0", "id": request_id,
                "error": {"code": code, "message": message}}

    def serve(self, stdin=None, stdout=None):
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except ValueError:
                continue
            response = self.handle(request)
            if response is None:
                continue
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--responses", help="JSON file of canned responses")
    parser.add_argument("--requests", help="Append every request here")
    args = parser.parse_args(argv)

    responses = {}
    if args.responses:
        with open(args.responses) as handle:
            responses = json.load(handle)
    Recorder(responses, args.requests).serve()


if __name__ == "__main__":
    main()
