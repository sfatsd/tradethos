"""Run one eval case against the fake broker, in an isolated agent.

**Why not the Agent tool.** A subagent inherits the parent's MCP servers, so
it would hold `place_equity_order` against the real brokerage. A case whose
prompt is "buy $50 of NVDA" would then spend real money, which is the exact
thing the fake exists to prevent. There is no per-spawn way to take those
tools away.

`claude -p --strict-mcp-config` does have one. With `--strict-mcp-config` the
agent sees only the servers named on the command line, so the live brokerage
is not merely discouraged, it is absent. That distinction matters here more
than usual: several of these cases measure whether an agent follows an
instruction, and an eval that relied on the agent obeying "do not touch the
real account" would be resting the safety of real money on the property it is
trying to measure.

**The server takes the real server's name on purpose.** The skills tell the
agent to call `review_equity_order` before `place_equity_order`, and the tool
names an agent sees are `mcp__<server>__<tool>`. Naming the fake after the
real server id means the skill text resolves against the fake with no edit, so
the eval exercises the shipped instructions rather than a rewritten copy.

Usage:

    python -m evals.run_case review-before-place
    python -m evals.run_case --all --skill trade-executor
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.graders import cases as case_registry          # noqa: E402
from evals.graders.transcript import (                    # noqa: E402
    RunArtifacts, Transcript)
from evals.graders.check_record_fills import (            # noqa: E402
    grade as grade_record_fills, index_orders)

# The live server's id. The fake answers to it so the skills resolve without
# being rewritten for the test.
SERVER_ID = "1975086b-2b49-4b1b-b657-097b3b1d7a24"
BASKET_CLI = ROOT / "skills" / "basket-manager" / "scripts" / "basket.py"


def write_mcp_config(directory, transcript, seed):
    """Write an MCP config that starts the fake and logs every call."""
    args = ["-m", "evals.fake_mcp.server", "--transcript", str(transcript)]
    if seed:
        args += ["--seed", str(seed)]
    config = {"mcpServers": {SERVER_ID: {
        "command": sys.executable, "args": args,
        "env": {"PYTHONPATH": str(ROOT)}}}}
    path = directory / "mcp.test.json"
    path.write_text(json.dumps(config, indent=2))
    return path


def write_seed(directory, scenario):
    if not scenario:
        return None
    path = directory / "seed.json"
    path.write_text(json.dumps(scenario))
    return path


def prepare_basket(data_dir, case):
    """Create the baskets a case expects before the agent starts."""
    created = []
    for key in ("basket", "second_basket"):
        spec = case.get(key)
        if not spec:
            continue
        result = subprocess.run(
            [sys.executable, str(BASKET_CLI), "--data-dir", str(data_dir),
             "create", spec["name"], "--symbols", spec["symbols"],
             "--account", "123456789"],
            capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            created.append(json.loads(result.stdout)["slug"])
    return created


def run(case, timeout=300, model=None, verbose=False):
    workspace = Path(tempfile.mkdtemp(prefix="eval-%s-" % case["name"]))
    transcript = workspace / "transcript.jsonl"
    data_dir = workspace / "tradethos"
    data_dir.mkdir()

    slugs = prepare_basket(data_dir, case)
    seed = write_seed(workspace, case.get("scenario"))
    config = write_mcp_config(workspace, transcript, seed)

    # `--strict-mcp-config` is the isolation: the agent gets only the servers
    # named here, so the live brokerage is absent rather than merely
    # discouraged.
    #
    # There is deliberately no `--disallowedTools` for the brokerage pattern.
    # The fake answers to the real server's id so the skills resolve against
    # it unchanged, which means any rule that blocks the real tools blocks
    # the fake by the same name. The first version added that flag as belt
    # and braces and the agent reported having no brokerage tools at all:
    # the safety net and the thing under test were indistinguishable.
    command = [
        "claude", "-p", case["prompt"],
        "--strict-mcp-config", "--mcp-config", str(config),
        "--allowedTools", "mcp__%s__*" % SERVER_ID, "Bash",
    ]
    if model:
        command += ["--model", model]

    started = time.time()
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=timeout, cwd=str(workspace),
                                env=dict(os.environ,
                                         TRADETHOS_DATA_DIR=str(data_dir)))
        final_message, failure = result.stdout, None
    except subprocess.TimeoutExpired:
        final_message, failure = "", "timed out after %ds" % timeout
    elapsed = time.time() - started

    artifacts = RunArtifacts(
        transcript=Transcript.from_file(transcript),
        events=_load_events(data_dir / "events.log.jsonl"),
        final_message=final_message,
        slug=slugs[0] if slugs else None)
    artifacts.orders_by_id = _orders_from_transcript(artifacts.transcript)
    artifacts.expected_order_ids = sorted(artifacts.orders_by_id)

    results = [check(artifacts) for check in case["assertions"]]
    if case.get("record_fills_grader"):
        results += grade_record_fills(
            artifacts.events, artifacts.orders_by_id,
            artifacts.expected_order_ids, artifacts.slug)

    return {"case": case["name"], "skill": case["skill"],
            "passed": bool(results) and all(r["passed"] for r in results)
                      and not failure,
            "failure": failure, "seconds": round(elapsed, 1),
            "tool_calls": len(artifacts.transcript),
            "tools_used": artifacts.transcript.tools(),
            # Kept for diagnosis. A run with no tool calls is ambiguous
            # until you read what the agent said: it may have refused, or
            # asked a question, or found no tools at all - and those are
            # three very different results.
            "final_message": final_message,
            "expectations": results, "workspace": str(workspace),
            "judgment": case.get("judgment")}


def _load_events(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()
            if line.strip()]


def _orders_from_transcript(transcript):
    """Recover the broker's orders from what it was asked and answered.

    The grader needs the broker's own record of each fill. The fake writes
    only the calls, so the ids come from the place calls the agent made.
    """
    orders = {}
    for call in transcript.calls:
        if call.get("tool") == "place_equity_order":
            order = (call.get("result") or {})
            if order:
                orders[order.get("id")] = order
    return orders


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", nargs="?", help="Case name to run")
    parser.add_argument("--all", action="store_true", help="Run every case")
    parser.add_argument("--skill", help="Restrict --all to one skill")
    parser.add_argument("--model", help="Model id to run the agent on")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--out", help="Write the JSON report here")
    args = parser.parse_args(argv)

    if args.all:
        selected = (case_registry.for_skill(args.skill) if args.skill
                    else case_registry.ALL_CASES)
    elif args.name:
        if args.name not in case_registry.BY_NAME:
            parser.error("unknown case %s" % args.name)
        selected = [case_registry.BY_NAME[args.name]]
    else:
        parser.error("give a case name, or --all")

    if not shutil.which("claude"):
        print("the claude CLI is not on PATH", file=sys.stderr)
        return 2

    reports = []
    for case in selected:
        report = run(case, timeout=args.timeout, model=args.model)
        reports.append(report)
        mark = "PASS" if report["passed"] else "FAIL"
        print("%-5s %-32s %5.1fs  %2d tool calls"
              % (mark, report["case"], report["seconds"],
                 report["tool_calls"]))
        for expectation in report["expectations"]:
            if not expectation["passed"]:
                print("        %s" % expectation["text"])
                print("          -> %s" % expectation["evidence"][:100])
        if report["failure"]:
            print("        run failed: %s" % report["failure"])
        if report["judgment"]:
            print("        needs a reader: %s" % report["judgment"])

    if args.out:
        Path(args.out).write_text(json.dumps(reports, indent=2))
    passed = sum(1 for r in reports if r["passed"])
    print("\n%d/%d cases passed" % (passed, len(reports)))
    return 0 if passed == len(reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
