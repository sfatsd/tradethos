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
| `run_case.py` | Runs one case in an isolated agent and grades it |
| `fake_mcp/recorder.py` | Serves canned responses, logs every request |
| `responses.py` | Builds the canned responses a case serves |
| `fake_mcp/state.py` | Shapes those responses, offline, before any run |
| `fake_mcp/server.py` | Tool declarations, shared with the recorder |
| `graders/cases.py` | The cases, as data |
| `graders/assertions.py` | Reusable checks over requests and answers |
| `graders/check_record_fills.py` | Compares the ledger to what the broker filled |
| `capture_fixtures.py` | Turns real MCP responses into a seed |
| `check_no_real_data.py` | Scans tracked content against a private list |
| `fixtures/` | Captured responses. Git-ignored: they hold real positions. |

## Why the fake decides nothing

The first version simulated a brokerage, and every defect this harness
produced came from that. It refused market orders after hours, a rule the
real broker does not have. A sell added shares and took cash. A dollar-amount
order sized itself off the quote and filled at the ask, so the notional was
out by the spread. Each one made a correct agent look wrong, which is the
most expensive kind of wrong result: it sends someone to debug a healthy
skill.

None of those bugs were in the assertions. Of the checks in `assertions.py`,
almost all read the request rather than any simulated state - was the review
called before the order, for that symbol; did the draft carry the right
account. The simulation was carrying very little and breaking often.

So the recorder hands back canned responses and logs every request.
`state.py` still shapes those responses, but offline and once, which keeps
the payload details that matter:

- `created_at` carries six fractional digits and `last_transaction_at` carries
  three. Code that parses one and not the other passes against a tidy fixture.
- A buy fills at the ask, not at the last trade price. If the fake returns one
  number for both, no test can tell a fill price from a quote price.
- `get_equity_orders` returns newest first. An agent that forwards ids in
  response order records history backwards.

One line is worth keeping sharp. *Deciding an outcome* - whether an order
fills, at what price, whether there is buying power - is simulation. *Not
contradicting the request* is not. A review that comes back for WDC when the
agent asked about NVDA is a lie, and a run that noticed refused to place,
correctly. So identity fields are echoed and nothing else is, and the echo
has to be complete: patching the header symbol while leaving the embedded
quote alone produced a fresh contradiction that a later run also caught.

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
python3 -m evals.check_no_real_data --staged   # the staged blobs
```

`--staged` reads the blob with `git show :<path>`, not the file on disk. The
first version took the names from `git diff --cached` and then opened those
paths, which is the wrong content: a secret can be staged and then edited out
of the working tree, and the scan would read the clean copy and report clean
while the index still held it. The two sources usually agree, which is what
made the bug quiet enough to ship.

A git failure exits 2, never 0. The same first version discarded git's exit
code, so running outside a repository produced an empty file list and a
confident "clean". A guard whose failure mode is silent success has the one
failure mode it cannot have.

Values shorter than five characters are rejected rather than matched, and
matching respects word boundaries, so `590.05` on the list does not fire on
`1590.055`. A guard that cries wolf is a guard people learn to skip.

A checkout with no `.private-values` passes trivially, which is correct for a
fresh clone and avoids training anyone to delete the hook.

Neither guard covers the case that caused the trouble here: a real value typed
straight into source, which never passes through the masker. That is what the
scanner is for, and it only works if the list is kept current.

## What is not built yet

The runs exist - `run_case.py` drives a real agent against the recorder. Two
things from the plan do not:

**A baseline.** Every case runs with the skill. Nothing runs the same prompt
without it, so no result yet separates "the skill caused this" from "the
model would have done it anyway".

**Repeat sampling.** Every number so far is one sample, and the variance is
real: a case flipped between runs, and `over-claimed-is-surfaced` passed
twice by matching a phrase in unrelated prose before anyone noticed it had
never built its own condition. The plan asks for three samples per case and
the evidence supports it.

Until both exist, treat a pass rate as a description of one run rather than a
measurement of a skill.

## What a run costs

Each case is a full agent session, so the unit that matters is turns, not
tool calls. A case with three MCP calls took eighty seconds and the
arithmetic did not work until the runner started counting turns: the missing
time was fifteen Bash and reasoning round-trips that nothing was recording.

`--output-format json` gives turns, API time and cost, and `run_case` reports
them per case and as a total:

```
PASS  verify-on-report                  78.9s  18 turns  3 mcp  $0.366
```

Rough shape at the time of writing: a basket case is 15-20 turns and about
$0.35, a trade case 4-9 turns and about $0.15. The whole suite is roughly 25
minutes and $6 for one sample of each case.

That settles where each layer belongs. The programmatic tests in `tests/` run
in about twenty seconds and cost nothing, so they are the per-commit gate.
The agent-in-the-loop suite is a pre-release check. Three samples per case,
which is what the plan asks for and what the variance seen so far justifies,
is around 75 minutes and $18 - worth doing deliberately, not on every push.
