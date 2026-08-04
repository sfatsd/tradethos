# Skill evaluation harness

The unit tests in `tests/` prove that `basket.py` does what its code says. They
cannot prove that an agent uses it correctly.

On 2026-08-03 all 283 tests passed while an agent wrote six events into the
live ledger with the wrong timestamp. The agent built the orders JSON by hand
instead of forwarding the `get_equity_orders` response. No unit test can find
that, because a unit test calls the function directly and never makes the
choice the agent made.

This harness tests that layer.

## Parts

| Path | Purpose |
|---|---|
| `fake_mcp/state.py` | A brokerage that holds state and moves no money |
| `fake_mcp/server.py` | An MCP server over stdio, with the real tool names |
| `graders/check_record_fills.py` | Compares the ledger to what the broker filled |
| `capture_fixtures.py` | Turns real MCP responses into a seed for the fake |
| `fixtures/` | Captured responses. Git-ignored: they hold real positions. |

## Why the fake holds state

A fake that replays one fixed file cannot test the sequence that matters.
`place_equity_order` has to mint an order id, and the `get_equity_orders` call
that follows has to return that order, filled, in the full shape the real API
uses. Place the order, read it back, record the fill: that is the sequence
that broke, so the fake has to support it.

Three details are reproduced on purpose, because each one hides a real bug
when a fake gets it wrong:

- `created_at` carries six fractional digits and `last_transaction_at` carries
  three. Code that parses one and not the other passes against a tidy fixture.
- A buy fills at the ask, not at the last trade price. If the fake returns one
  number for both, no test can tell a fill price from a quote price.
- `get_equity_orders` returns newest first. An agent that forwards ids in
  response order records history backwards.

## Run the self-tests

```bash
python3 -m unittest discover -s tests -p test_eval_harness.py -v
```

These test the harness, not the skills. The negative cases carry the weight: a
grader that never fails is worse than no grader, because it reports green
while the defect ships.

Three agent behaviours run against the same filled orders:

| Behaviour | What it does | Which assertion catches it |
|---|---|---|
| well-behaved | Forwards the raw response | none, it passes |
| trimming | Strips the timestamps | completeness, because nothing records |
| pre-fix | Writes what the old code wrote | the timestamp, and only that one |

The `pre-fix` case is the important one. It passes three assertions out of
four: the share counts, the prices and the completeness are all correct. That
is exactly why the original defect was invisible. If that test ever starts to
pass all four, the grader has stopped working.

## Capture fixtures from the real account

The MCP tools belong to the agent, so this script cannot call Robinhood. The
agent makes the call and pipes the response in:

```bash
python3 -m evals.capture_fixtures --tool get_equity_quotes < quotes.json
python3 -m evals.capture_fixtures --make-seed
```

Account numbers are replaced on the way in. Share counts and cash are not, so
`evals/fixtures/` stays git-ignored and the committed defaults in `state.py`
are synthetic.

Re-capture before a release and read the diff. A change in the shape of a
response is worth knowing about, so do not overwrite one without looking.

## Keeping real data out

Two guards, each for a failure that already happened once.

**`capture_fixtures.py` verifies its own masking.** The first time it ran, the
placeholder held a real account number, so the substitution mapped that number
to itself. The capture was masked in form and unmasked in fact, and nothing in
the run said so. `verify_masked` compares the account values before and after
and refuses to write a file when none of them changed.

**`check_no_real_data.py` scans tracked files against a private list.** A
repository cannot carry the list of values it must never contain, so the list
lives in `.private-values`, which is git-ignored. That inverts the usual
approach: a pattern-based scanner has to guess what a real account number
looks like, and it either misses the ordinary-looking ones or drowns in false
positives from test fixtures. Matching against values already known to be real
gives an exact answer.

```bash
python3 -m evals.check_no_real_data            # every tracked file
python3 -m evals.check_no_real_data --staged   # only what is staged
```

The staged form is the one worth putting in a pre-commit hook, because it asks
the question while the answer still costs nothing. A checkout with no
`.private-values` passes trivially, which is correct for a fresh clone and
avoids training anyone to delete the hook.

Neither guard covers the case that caused the trouble here: a real value typed
straight into source, which never passes through the masker. That is what the
scanner is for, and it only works if the list is kept current.

## What is not built yet

The agent-in-the-loop runs. Each eval needs a subagent with the skill and a
second one without it, for a baseline. See
`docs/superpowers/plans/2026-08-03-skill-evaluation-suite.md` for the eval
cases, the per-skill assertions and the phases.
