"""An MCP server that speaks for the fake broker.

The tool names here match the real Robinhood MCP server exactly. That is the
point: the skills tell the agent to call `review_equity_order` before
`place_equity_order`, and an eval is only meaningful if the agent can follow
that instruction literally. A fake with its own names would test a different
skill than the one that ships.

Every call is appended to a transcript file. Most assertions in this suite
ask what the agent did and in which order, and the final message usually
hides that. The transcript is the evidence.

Run it as an MCP server over stdio:

    python -m evals.fake_mcp.server --transcript /tmp/calls.jsonl
"""

import argparse
import json
import sys

from . import state as broker_state


PROTOCOL_VERSION = "2024-11-05"

# Only the arguments an assertion inspects are described. A looser schema
# keeps the fake from rejecting a call the real server would accept, which
# would turn a skill bug into a harness bug.
TOOLS = [
    {"name": "get_accounts",
     "description": "List the user's brokerage accounts.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "get_portfolio",
     "description": "Portfolio value breakdown and buying power.",
     "inputSchema": {"type": "object",
                     "properties": {"account_number": {"type": "string"}},
                     "required": ["account_number"]}},
    {"name": "get_equity_positions",
     "description": "List open equity positions.",
     "inputSchema": {"type": "object",
                     "properties": {"account_number": {"type": "string"}},
                     "required": ["account_number"]}},
    {"name": "get_equity_quotes",
     "description": "Real-time quotes and the prior close.",
     "inputSchema": {"type": "object",
                     "properties": {"symbols": {"type": "array",
                                                "items": {"type": "string"}}},
                     "required": ["symbols"]}},
    {"name": "get_equity_tradability",
     "description": "Check whether symbols can be traded.",
     "inputSchema": {"type": "object",
                     "properties": {"account_number": {"type": "string"},
                                    "symbols": {"type": "array",
                                                "items": {"type": "string"}}},
                     "required": ["account_number", "symbols"]}},
    {"name": "review_equity_order",
     "description": "Simulate an order without placing it.",
     "inputSchema": {"type": "object",
                     "properties": {"account_number": {"type": "string"},
                                    "symbol": {"type": "string"},
                                    "side": {"type": "string"},
                                    "type": {"type": "string"},
                                    "dollar_amount": {"type": "string"},
                                    "quantity": {"type": "string"},
                                    "limit_price": {"type": "string"}},
                     "required": ["account_number", "symbol", "side",
                                  "type"]}},
    {"name": "place_equity_order",
     "description": "Place a real equity order.",
     "inputSchema": {"type": "object",
                     "properties": {"account_number": {"type": "string"},
                                    "symbol": {"type": "string"},
                                    "side": {"type": "string"},
                                    "type": {"type": "string"},
                                    "dollar_amount": {"type": "string"},
                                    "quantity": {"type": "string"},
                                    "ref_id": {"type": "string"},
                                    "market_hours": {"type": "string"}},
                     "required": ["account_number", "symbol", "side",
                                  "type"]}},
    {"name": "get_equity_orders",
     "description": "Fetch equity orders, newest first.",
     "inputSchema": {"type": "object",
                     "properties": {"account_number": {"type": "string"},
                                    "order_id": {"type": "string"}},
                     "required": ["account_number"]}},
    {"name": "get_equity_fundamentals",
     "description": "Valuation and company fundamentals.",
     "inputSchema": {"type": "object",
                     "properties": {"symbols": {"type": "array",
                                                "items": {"type": "string"}}},
                     "required": ["symbols"]}},
    {"name": "get_equity_historicals",
     "description": "Historical price series.",
     "inputSchema": {"type": "object",
                     "properties": {"symbols": {"type": "array",
                                                "items": {"type": "string"}},
                                    "interval": {"type": "string"},
                                    "span": {"type": "string"}},
                     "required": ["symbols"]}},
    {"name": "get_equity_technical_indicators",
     "description": "RSI, MACD and moving averages.",
     "inputSchema": {"type": "object",
                     "properties": {"symbols": {"type": "array",
                                                "items": {"type": "string"}},
                                    "indicators": {"type": "array",
                                                   "items": {"type":
                                                             "string"}}},
                     "required": ["symbols"]}},
    {"name": "get_scanner_filter_specs",
     "description": "The catalogue of valid screener filters and presets.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "create_scan",
     "description": "Create a screener configuration.",
     "inputSchema": {"type": "object",
                     "properties": {"name": {"type": "string"},
                                    "filters": {"type": "object"},
                                    "preset": {"type": "string"}},
                     "required": ["name"]}},
    {"name": "run_scan",
     "description": "Run a screener and return matches.",
     "inputSchema": {"type": "object",
                     "properties": {"scan_id": {"type": "string"},
                                    "preset": {"type": "string"}}}},
]


TOOL_NAMES = frozenset(t["name"] for t in TOOLS)


class Server(object):

    def __init__(self, broker, transcript_path=None):
        self.broker = broker
        self.transcript_path = transcript_path

    def _log(self, entry):
        if not self.transcript_path:
            return
        # Append and flush per call. A run that crashes half way is exactly
        # the run whose transcript matters most, so nothing is buffered.
        with open(self.transcript_path, "a") as handle:
            handle.write(json.dumps(entry) + "\n")

    def call_tool(self, name, arguments):
        # Resolve only names this server declares. Reading the name straight
        # off the broker with getattr would also reach its ordinary methods:
        # a call named `__init__` would reset the broker's state in the
        # middle of a run, and `record_call` would let the agent write its
        # own transcript. The point of this harness is to be trusted about
        # what the agent did, so it answers to the published list and
        # nothing else.
        if name not in TOOL_NAMES:
            raise broker_state.BrokerError("Unknown tool %s" % name)
        self.broker.record_call(name, arguments)
        handler = getattr(self.broker, name, None)
        if handler is None:
            raise broker_state.BrokerError(
                "Tool %s is declared but not implemented" % name)

        entry = {"tool": name, "arguments": arguments}
        try:
            result = handler(**arguments)
        except Exception as error:
            # A refused call is still a call. Dropping it would hide the
            # attempt from every ordering assertion, and "did the agent try
            # to place this?" is exactly what several cases ask.
            entry["error"] = str(error)
            self._log(entry)
            raise
        if name == "place_equity_order":
            # The grader compares the ledger against what the broker
            # actually filled, so the order has to survive in the record
            # the grader reads rather than only in the agent's reply.
            entry["result"] = (result.get("data") or {}).get("order")
        self._log(entry)
        return result

    def handle(self, request):
        method = request.get("method")
        request_id = request.get("id")

        if method == "initialize":
            return self._ok(request_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "tradethos-fake", "version": "0.1.0"}})

        if method == "tools/list":
            return self._ok(request_id, {"tools": TOOLS})

        if method == "tools/call":
            params = request.get("params") or {}
            name = params.get("name")
            arguments = params.get("arguments") or {}
            try:
                payload = self.call_tool(name, arguments)
            except TypeError as error:
                # A wrong or missing argument is an agent mistake, and the
                # real server answers it with an error rather than dropping
                # the connection. Letting it propagate would kill the run
                # and make an agent failure read as infrastructure failure,
                # which is the most expensive kind of wrong result: it
                # discredits the eval instead of the agent.
                return self._ok(request_id, {
                    "content": [{"type": "text",
                                 "text": json.dumps(
                                     {"error": "Bad arguments for %s: %s"
                                               % (name, error)})}],
                    "isError": True})
            except broker_state.TransportError as error:
                # Surfaced as a protocol error, not a tool result. A retry
                # is the correct response, and the agent should reuse its
                # ref_id when it retries.
                return self._error(request_id, -32000, str(error))
            except broker_state.BrokerError as error:
                return self._ok(request_id, {
                    "content": [{"type": "text",
                                 "text": json.dumps({"error": str(error)})}],
                    "isError": True})
            return self._ok(request_id, {
                "content": [{"type": "text",
                             "text": json.dumps(payload)}]})

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


def build_broker(seed_path=None):
    if not seed_path:
        return broker_state.Broker()
    return broker_state.Broker(**broker_state.load_seed(seed_path))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", help="JSON file of Broker constructor args")
    parser.add_argument("--transcript", help="Append every tool call here")
    args = parser.parse_args(argv)
    Server(build_broker(args.seed), args.transcript).serve()


if __name__ == "__main__":
    main()
