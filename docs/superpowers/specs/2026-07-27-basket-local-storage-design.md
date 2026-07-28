# Design: Local Basket Storage (v0.3)

**Date:** 2026-07-28
**Status:** Approved. Not yet implemented.
**Supersedes:** The cloud watchlist storage model in v0.2.x.

---

## 1. Purpose

Tradethos stores each custom basket in a Robinhood watchlist description.
That field holds 256 characters. This limit is too small.

A measurement of the live `Basket: Magnificent 7 Index` watchlist shows the
problem. The basket holds 7 symbols. Its encoded description is 240 of 256
characters. An eighth symbol makes the basket impossible to save.

This design moves basket data to local files. Local files remove the size
limit. They also remove the compression code, the attribution encoding, and
the snapshot update code that caused every defect found in the v0.2.2 review.

---

## 2. Requirements

The user gave four requirements.

1. Create a custom basket of stocks after research.
2. Give each stock in a basket a custom target weight.
3. Track the performance of the money invested in a basket. Track the
   performance of each stock in the basket.
4. Let two or more baskets hold the same stock.

Requirement 3 needs a per-basket share ledger. The user calls this a
sub-portfolio. A basket must know how many shares belong to it. It must not
read the full account position, because the user can hold the same stock
outside the basket.

Requirement 4 makes the ledger harder. The same stock can belong to more than
one basket. Each basket must record its own share count for that stock.

Requirement 1 sets the main journey. The user researches a stock, adds it to
a basket, and buys it. The design must support a trade for one stock and a
trade for a whole basket.

---

## 3. Why the cloud model failed

The v0.2.x model compresses basket metadata with zlib. It then encodes the
result with Base64. It writes the result to the watchlist description.

Measurements show that this method saves very little space.

| Step | Size |
|---|---|
| Raw JSON | 258 bytes |
| After zlib | 175 bytes (−32%) |
| After Base64 | 236 characters (+35%) |
| Final, with the `Z64:` prefix | 240 characters |

Compression saves 83 bytes. Base64 adds 61 bytes back. The net gain over plain
JSON is 18 characters.

Two facts explain this result. First, the payload is small. zlib needs a large
window to find repeated text. Second, the data has high entropy. Share counts
and prices are almost random digits. Compression cannot reduce them.

An earlier benchmark claimed a 74% reduction and a capacity of 30 to 40
symbols. That benchmark used placeholder data. Every symbol had the same
weight, the same share count, and the same price. zlib removes that repetition
almost completely. Real baskets have different tickers and different prices.
The benchmark result does not apply to them.

The table below compares both data types.

| Symbols | Synthetic data | Real data | Real data fits? |
|---|---|---|---|
| 5 | 124 chars | 196 chars | Yes |
| 10 | 140 chars | 284 chars | No |
| 20 | 176 chars | 452 chars | No |
| 30 | 208 chars | 620 chars | No |

At 5 symbols, compression makes the payload larger than plain JSON.

---

## 4. Decisions

The user made these decisions during the design session.

| Decision | Choice |
|---|---|
| Cross-device support | Local only. One machine. |
| Robinhood watchlists | Remove them from the basket workflow. |
| File location | `~/.tradethos/` |
| History | One event log holds every change. It is the source of truth. |
| Reads | Every read replays the log. The store has no cache. |
| Snapshot files | Exports for the user. No command reads them. |
| `order_id` scope | Unique across all baskets. One order funds one basket. |
| Trade recording | Only from order data, by order id. Never from typed numbers. |
| Trade scope | A basket trade and a single-stock trade both end in `record-fills`. |
| Outside sales | The tool detects them. The user repairs them by recording the sale order. |
| Manual entry | No command takes a typed share count or a typed price. |
| Target weights | Whole numbers. Always sum to exactly 100. The tool normalizes every set. |
| Migration source | Cloud snapshots plus order history. |
| Write method | One command-line tool. The agent never edits the files. |
| Log rotation | Do not build it now. |

---

## 5. Data model

The store uses one event log and one snapshot file for each basket.

```
~/.tradethos/
  events.log.jsonl              All events. Append only. The source of truth.
  baskets/
    magnificent-7-index.json    A snapshot. An export for the user.
```

### 5.1 The event log

The log records every change as an event. It records trades. It also records
changes to a basket definition. The tool only appends to this file. It never
rewrites the file and it never deletes a line.

The tool computes every basket state by reading the log from the start. A
snapshot holds no fact that the log does not hold.

### 5.2 Why one log

Robinhood stores a complete order history. That history has no basket tag.
The local store must record which basket owns each order. This mapping is the
one fact that only the local store holds.

One log serves this mapping better than one log for each basket.

The user chose global uniqueness for `order_id`. One order funds one basket
only. With one log for each basket, the tool must read every log before each
write. With one log, the tool reads one file.

The log also records definition changes. A separate log for each basket cannot
record an event that moves a holding between two baskets.

Deletion is an event, not a file operation. The tool appends a
`basket_deleted` event. The replay then omits that basket. The history
stays. The append-only property stays.

### 5.3 Why reads replay the log

The log is small. A year of active use produces hundreds of lines. A replay
of the whole log takes milliseconds. Every read command therefore replays the
log. The store has no cache.

This choice removes a class of machinery. A cache can go stale, so a cache
needs a fingerprint, a staleness check, and a repair path. A replay cannot go
stale. The design needs none of those parts.

The tool still writes a snapshot file for each basket. The snapshot is an
export for the user. It gives the user a small readable file to inspect and
to copy. No command reads a snapshot. A missing or damaged snapshot is
therefore harmless. The `export` command writes it again.

### 5.4 Event types

Each event holds a timestamp, a type, a schema version, and a basket slug.

| Type | Purpose | Command |
|---|---|---|
| `basket_created` | Record a new basket, its name, its description, its account, and its threshold. | `create` |
| `basket_updated` | Change the name, the description, or the rebalance threshold. | `set-name`, `set-description`, `set-threshold` |
| `basket_deleted` | Mark a basket as removed. | `delete` |
| `holding_added` | Add a symbol with a target weight and a thesis. | `create`, `add-holding` |
| `holding_removed` | Remove a symbol from the basket definition. | `remove-holding` |
| `weight_changed` | Record the old weight and the new weight. | `set-weight`, `set-weights`, `add-holding`, `remove-holding` |
| `thesis_changed` | Record a new thesis for a holding. | `set-thesis` |
| `buy` | Record a purchase. | `record-fills` |
| `sell` | Record a sale. | `record-fills` |

`record-fills` produces both trade event types. It reads the `side` field of
each order. A `side` of `buy` produces a `buy` event. A `side` of `sell`
produces a `sell` event. Section 6.2 gives the reason.

Every event type must have a command that produces it. Every field in a
snapshot must come from an event. Section 6.1 lists the commands.

Example lines:

```
{"v":1,"ts":"2026-07-23T12:22:00Z","type":"basket_created","slug":"magnificent-7-index","name":"Magnificent 7 Index","description":"Mega-cap tech leaders","account_number":"000000000","rebalance_threshold_pct":5.0}
{"v":1,"ts":"2026-07-23T12:22:01Z","type":"holding_added","slug":"magnificent-7-index","symbol":"NVDA","weight":14,"thesis":"GPU compute leader"}
{"v":1,"ts":"2026-07-23T19:25:23Z","type":"buy","slug":"magnificent-7-index","symbol":"NVDA","shares":0.068659,"price":208.1299,"amount":14.29,"order_id":"REDACTEDA1-0000-0000-0000-000000000001"}
{"v":1,"ts":"2026-07-25T09:00:00Z","type":"weight_changed","slug":"magnificent-7-index","symbol":"NVDA","from":14,"to":20}
{"v":1,"ts":"2026-07-26T10:00:00Z","type":"basket_updated","slug":"magnificent-7-index","field":"rebalance_threshold_pct","from":5.0,"to":3.0}
```

The `v` field holds the event schema version. The log keeps every line
forever. A reader must therefore accept an old shape. Section 9.5 gives the
rule.

### 5.5 What the event log adds

The previous design stored only trades. It did not record a change to a
definition. A user could change a target weight, and the store kept no record
of the old value or the date.

The event log records these changes. The user can now answer a question such
as "when did I raise the NVDA target from 14 to 20 percent". This question
matters, because the user reviews these decisions later.

### 5.6 Snapshot file

```json
{
  "schema_version": 2,
  "name": "Magnificent 7 Index",
  "slug": "magnificent-7-index",
  "description": "Mega-cap tech leaders that drive AI infrastructure",
  "account_number": "000000000",
  "rebalance_threshold_pct": 5.0,
  "created_at": "2026-07-23T12:22:00Z",
  "updated_at": "2026-07-27T13:30:01Z",

  "holdings": [
    {
      "symbol": "NVDA",
      "target_weight_pct": 14,
      "thesis": "GPU compute leader that powers generative AI",
      "position": {
        "shares": 0.068659,
        "avg_cost": 208.13,
        "total_invested": 14.29,
        "realized_pnl": 0.0
      }
    }
  ],

  "totals": {
    "total_invested": 100.00,
    "realized_pnl": 0.0,
    "built_at": "2026-07-27T13:30:01Z"
  }
}
```

### 5.7 Field rules

- The event log is the source of truth. Every read replays it. A snapshot is
  an export; no command reads it.
- The tool writes a new snapshot after each write. A failed snapshot write
  loses nothing.
- `order_id` holds the Robinhood order identifier. An order identifier is
  unique across all baskets. One order funds one basket only.
  - **The replay ignores a `buy` or `sell` event whose `order_id` already
    appeared in an earlier event.** The first event wins. A duplicate line is
    therefore harmless. This rule holds even when a check fails or two
    sessions write at the same time.
  - **The replay must report every event that it ignores.** The tool prints
    the line number, the `order_id`, and both baskets. A silent skip is
    unsafe. A duplicate in the same basket is a harmless retry. A duplicate
    in a different basket means that one basket lost a claim, and the user
    must see that.
  - The tool also scans the log before it writes. This scan gives a clear
    message instead of a silent skip. The scan is an improvement to the
    message, not the guarantee. Section 9.2 gives the reason.
  - If the identifier exists in the same basket, the tool changes nothing. It
    exits with code 0 and reports `already_recorded`. An agent can retry a
    failed call safely.
  - If the identifier exists in a different basket, the tool refuses the
    command. It exits with code 1 and names the other basket.
  - These rules prevent the double-count defect found in the review.
- `account_number` records the account that holds the trades. The user has two
  accounts. Only account `000000000` permits agent access. `record-fills`
  must refuse an order that came from a different account.
- `price` holds the fill price. The tool reads `average_price` from the
  order. It must not read the `price` field of an order. That field holds
  the limit price. Live data shows a difference between the two. One order
  shows a `price` of 206.80 and an `average_price` of 208.04.
- A `buy` event and a `sell` event hold `shares`, `price`, and `amount`. **The
  replay uses `shares` multiplied by `price`. It ignores `amount`.** The
  `amount` field records the dollar amount that the user requested. A dollar
  order shows the difference. One live order requests 10.00 dollars and fills
  0.048067 shares at 208.04 dollars, which is 10.0006 dollars. Two replays
  must always agree, so exactly one field can be authoritative. The `amount`
  field stays in the log for the record.

### 5.8 Average cost method

The tool uses the average cost method.

On a buy:

```
new_total_cost = old_total_cost + (shares * price)
new_shares     = old_shares + shares
new_avg_cost   = new_total_cost / new_shares
```

On a sell:

```
realized_pnl   = (price - avg_cost) * shares_sold
new_shares     = old_shares - shares_sold
new_total_cost = new_shares * avg_cost
avg_cost       = unchanged
```

When the user sells all shares, the tool sets `shares` and `avg_cost` to zero.
It keeps the accumulated `realized_pnl`.

### 5.9 Overlapping baskets

Requirement 4 becomes simple in this model. Every event carries a `slug`. The
same stock can appear in two baskets, because the two sets of events stay
separate. The design needs no attribution encoding and no exception map.

Two rules connect the baskets.

First, one order funds one basket only. To hold the same stock in two baskets,
the user must place two orders. The agent must tell the user this rule before
it places an order for a stock that another basket already holds. Section 5.7
gives the technical rule.

Second, the sum of a stock's shares across all baskets must not be greater
than the account position for that stock. The `verify` command checks this
rule. The check also finds shares that the user holds outside any basket.
Section 6.6 gives the three states of that check.

### 5.10 Trades are recorded from orders only

**The tool records a trade only from order data.** `record-fills` reads the
symbol, the share count, the `average_price`, and the identifier from the
order itself. No command takes a typed share count or a typed price. Every
money defect in the v0.2.2 review came from a number that the agent copied or
typed. This rule is the safety mechanism of the design.

The rule limits the mechanism, not the scope. The user can buy a whole basket
through `plan-buy`. The user can also buy one stock for one basket. Both
journeys end in the same call: `record-fills` with the order identifiers.
Section 7 gives both journeys.

The user can hold shares of one stock inside a basket and outside it. The
live account shows this case. The Magnificent 7 basket holds 0.068659 NVDA
shares. The account holds 0.116726 NVDA shares. `plan-sell --all` sells only
the basket's 0.068659 shares. The other 0.048067 shares stay outside every
basket.

A trade outside every basket needs no record. The user can buy more of a
stock at any time. Those shares stay outside every basket, and the baskets do
not change.

A sale outside a basket can make a basket record wrong. The user can sell
shares that a basket claims. Section 6.6 gives the check that finds this
state. Section 7.4 gives the repair: the user records the sale order into the
basket, so the record follows the real trade at the real price.

---

## 6. Command-line tool

The file `basket.py` provides all write operations. No other code writes to
the store. The agent calls this tool. The agent never edits a file.

Every write command performs the same two steps. It appends one event or more
to the log. It then writes a new snapshot for the baskets that changed.

### 6.1 Commands

Definition commands:

```
create <name> --symbols NVDA:14,MSFT:14 [--description] [--account] [--threshold]
add-holding <slug> --symbol NVDA --weight 12 [--thesis] [--fill MODE] [--dry-run]
remove-holding <slug> --symbol NVDA [--fill MODE] [--dry-run]
set-weight <slug> --symbol NVDA --weight 20 [--fill MODE] [--dry-run]
set-weights <slug> --weights NVDA:13,MSFT:13,AAPL:12,... [--dry-run]
set-thesis <slug> --symbol NVDA --thesis "..."
set-name <slug> --name "..."
set-description <slug> --description "..."
set-threshold <slug> --threshold 3.0
delete <slug>
```

Ledger commands:

```
plan-buy     <slug> --amount 100 [--prices '{"NVDA":210.00}']
plan-sell    <slug> (--amount 100 | --all) [--prices '{"NVDA":210.00}']
record-fills <slug> --orders-json '<get_equity_orders response>'
                    --order-ids <id1,id2,...> --account 000000000
                    [--cap-at-held]
```

### 6.2 Recording trades

`record-fills` is the only command that writes a trade. It accepts the raw
`get_equity_orders` response. It also requires `--order-ids`. The tool
records **only** the orders in that list. It reads the symbol, the filled
quantity, the `average_price`, and the order identifier from each order. It
appends one event for each order.

The `--order-ids` list is not optional. Selection by symbol alone is unsafe,
because the user can hold a stock outside a basket. Section 8.2 gives a real
example. One NVDA order belongs to the Magnificent 7 basket. A later NVDA
order does not. A tool that selects by symbol adds both orders to the basket.
The basket then claims shares that it does not own. This defect is the same
class of defect that the v0.2 design produced.

**The `--order-ids` list is a claim from the agent. The tool cannot check the
user's intent.** The tool records any order that the list assigns to the
basket. The guards stay the same in every case. The account must match. An
identifier that another basket holds is refused. A sell cannot exceed the
shares held. Check 3 of `verify` finds a claim that does not match the
account. The rule that the agent passes only the orders it placed for the
basket is a rule for the agent. The basket-manager skill states it.

One identifier is a valid list. A single-stock purchase for a basket is one
order, and `record-fills` records it in the same way as a batch of seven.
Section 7.3 gives that journey.

`record-fills` also requires `--account`. The tool refuses the command when
that value differs from the `account_number` of the basket. The user has two
accounts, and an order from the wrong account gives a wrong position.

This design removes a class of defect. The agent never copies numbers between
tools. The tool reads `average_price` from the order itself, so the agent
cannot pass the limit price by mistake. A command that took a share count and
a price would let the agent pass the limit price. The tool could not detect
that error. No such command exists.

`record-fills` skips any order whose identifier already exists in the log.
A repeated call therefore changes nothing.

Partial results are normal. Some orders fill and others do not. `record-fills`
records the filled orders. It reports every identifier that it did not record,
and it gives the reason. The basket then holds a partial position, and `show`
reports the gap against the target weights.

**`record-fills` skips a bad order. It does not refuse the batch.** A
refusal in section 6.7 rejects a whole command. A trade batch is different.
A batch can hold six good fills and one bad order. Those six fills are real
trades. The tool must not discard them.

The tool therefore applies each order on its own. It records the orders that
pass. It skips the orders that fail, and it gives a reason for each one. A
sell that exceeds the shares held is such a failure.

The exit code follows this rule.

| Result | Exit code |
|---|---|
| The tool recorded every order. | 0 |
| The tool recorded some orders and skipped others. | 0 |
| The tool recorded no order. | 1 |
| The account or the arguments are wrong. | 1 |

Exit code 0 therefore means "the log is now correct". It does not mean "every
order succeeded". The agent must read the report and tell the user about every
skipped order.

**The `--cap-at-held` option handles a sale that mixes basket shares and
outside shares.** A sell order can hold more shares than the basket claims.
`record-fills` skips such an order by default. With `--cap-at-held`, the tool
records a sell of the shares that the basket holds. The price still comes
from the order. The tool computes the capped share count. The agent must
state that count to the user before it uses this option. Section 7.4 uses it
in the repair journey.

The option is valid only when `--order-ids` holds exactly one identifier. The
tool refuses it in a batch. A cap across many orders would hide real changes
in one flag.

### 6.3 The planning commands

`plan-buy` and `plan-sell` compute the orders for a whole-basket trade. They
are conveniences, not gates. They write nothing.

`plan-buy` divides the amount by the target weights. It returns the dollar
amount for each symbol. It returns the share count when the caller supplies
prices. The tool owns this arithmetic. The agent must not compute the
allocation.

`plan-sell` has two modes.

`--all` returns every holding and its full share count. This mode exits the
basket.

`--amount` returns a share count for each holding. The tool divides the amount
by the **current market value** of each holding, not by the target weight. A
proportional sale keeps the current weights unchanged. The caller must supply
`--prices` for this mode.

The tool refuses an amount that is greater than the current value of the
basket. It reports both numbers and it suggests `--all`. A silent reduction of
the amount would surprise the user.

`--all` also accepts `--prices`. The tool then returns the estimated proceeds
for each holding and for the basket. The agent must not compute that estimate,
because the tool owns this arithmetic.

Both commands refuse an empty basket. The message must tell the user to add a
holding.

A sale that moves the basket toward its target weights is a rebalance. That
operation is out of scope. Section 14 records this decision.

### 6.4 Read and maintenance commands

Read commands:

```
list
show <slug> [--prices '{"NVDA":210.00}']
history [<slug>] [--symbol NVDA] [--since 2026-01-01]
```

Every read command replays the log. Section 5.3 gives the reason.

`history` prints the events for a basket in date order. This command answers
questions about past decisions, such as a change to a target weight.

Maintenance commands:

```
export [<slug>]
verify [<slug>] [--positions '<get_equity_positions output>']
backup [--to <directory>]
```

`export` writes the snapshot file for one basket, or for every basket when
the caller gives no slug. A user can delete the whole `baskets/` directory
and then run `export` to restore it.

`backup` copies `events.log.jsonl` to a timestamped file. The default
directory is `~/.tradethos/backups/`. The command then writes the event count
and the time into `~/.tradethos/backup.marker`.

**The tool runs `backup` by itself.** After a successful write, `basket.py`
reads the marker. It runs a backup when the log holds 20 more events than the
marker records. It also runs a backup when no marker exists. The backup is a
file copy of a small file, so this step is cheap.

The backup must hold the same lock that a write holds. A copy without the
lock can read the log while another session appends to it. The copy would
then hold a torn last line. Section 6.8 gives the lock.

An automatic backup makes the copy happen. The `verify` warning in section
6.6 is a fallback for the case where the automatic backup fails. A rule that
only warns would leave the user with no copy.

The tool prints JSON by default. The `--format table` option prints a table
for the user. This matches the existing scripts.

### 6.5 The weight invariant

**A target weight is a whole number of percent. The weights of a basket always
sum to exactly 100.** This rule holds after every command. No command can
leave a basket in another state.

The rule removes a class of question. A basket always describes a full
allocation. The `show` command never reports an unallocated remainder.
`plan-buy` always allocates the full amount. Cash is money that the user did
not put into the basket, not a part of the basket.

Whole numbers also remove the rounding questions that decimals create. A
target weight is a statement of intent, and one percent is a fine enough step
for that purpose.

#### The tool normalizes every weight set

Whole numbers cannot express every equal split. Seven equal holdings need
14.2857 percent each. Seven weights of 14 sum to 98.

The tool therefore uses the largest remainder method.

1. It scales the given weights so that they sum to 100.
2. It takes the whole part of each result.
3. It counts the shortfall from 100.
4. It adds one percent to each holding with the largest fractional part, until
   the total reaches 100. A tie breaks by symbol, in alphabetical order.

The Magnificent 7 basket shows the result. `AAPL` and `AMZN` hold 15 percent.
The other five holdings hold 14 percent. The total is exactly 100.

The tool reports every weight that it changed. The agent must show that report
to the user. The tool owns this arithmetic, in the same way that it owns the
allocation arithmetic of `plan-buy`.

Normalization also accepts a ratio. The input `NVDA:2,MSFT:1` gives 67 percent
and 33 percent. The user can therefore think in ratios or in percentages.

A basket holds at most 100 holdings, because every weight is at least 1.

#### Every weight command keeps the invariant

| Command | Effect |
|---|---|
| `create` | Normalizes the given weights to 100. Each `holding_added` event carries the normalized weight, so `create` writes no `weight_changed` event. |
| `set-weights` | Takes a weight for every holding. Normalizes to 100. |
| `set-weight` | Sets one weight. Scales every other holding to fill the rest. |
| `add-holding` | Gives the new holding its weight. Scales the others down. |
| `remove-holding` | Scales the remaining holdings up. |

`set-weights` needs a weight for every holding. The tool refuses a list that
omits a holding, and it names the missing symbols. It also refuses a symbol
that the basket does not hold.

`set-weight` changes weights that the user did not name. That result is
unavoidable under this invariant. Section 7.5 gives the rule that protects the
user from a surprise.

`add-holding` and `remove-holding` need no `--rescale` option. Rescaling is
the only correct behaviour, so it is always on.

#### The fill mode

The tool must distribute a change across the other holdings. Two methods give
different results, so the caller chooses one.

`--fill proportional` keeps the ratios between the other holdings. A holding
with twice the weight of another keeps twice the weight. This mode is the
default, because it preserves the decisions that the user already made.

`--fill equal` gives every other holding the same weight. This mode suits a
basket that the user holds in equal weights.

An example shows the difference. A basket holds NVDA at 50 percent, MSFT at 30
percent, and AAPL at 20 percent. The command `set-weight NVDA 20` leaves 80
percent for the other two holdings.

| Mode | MSFT | AAPL |
|---|---|---|
| `proportional` | 48 | 32 |
| `equal` | 40 | 40 |

#### The dry-run option

`--dry-run` prints the complete weight set that the command would write. It
appends no event and it changes no file. The agent uses this option to show
the result before it asks the user for a decision.

Removing the last holding of a basket leaves no weights. The basket is then
empty, and the invariant does not apply. The tool allows this state, because
the user may want to rebuild the basket.

### 6.6 The verify command

`verify` does three checks.

1. It replays the log. It reports every corrupt line and every event that the
   replay ignored.
2. It reads `~/.tradethos/backup.marker`. It warns when the marker is absent.
   It also warns when the log holds more events than the marker records.
3. It compares the total shares for each symbol across all baskets to the
   account position, when the caller supplies `--positions`.

Check 3 needs the network. Checks 1 and 2 do not.

Check 3 is the main safety net of this design. The tool records a trade only
when the user assigns it to a basket. The user can still sell the same shares
outside a basket, and the tool cannot prevent that sale. Check 3 finds the
result.

The check reports three states for each symbol.

| State | Meaning |
|---|---|
| The claims equal the position. | The records are correct. |
| The claims are below the position. | The user holds shares outside every basket. This state is normal. |
| The claims are above the position. | The user sold shares that a basket claims. The records are wrong. |

The third state needs a repair. Section 7.4 gives the journey. The tool must
not repair this state by itself, because the repair needs a decision from the
user.

### 6.7 Validation rules

The tool owns these rules. No skill repeats them.

- An input weight must be a positive number. It may hold decimals, because an
  input can be a ratio. The tool applies no upper bound to an input. The set
  `NVDA:200,MSFT:100` gives 67 and 33.
- The tool refuses a weight that is negative, zero, or not a number.
- **A stored weight is a whole number. The weights of a basket always sum to
  exactly 100.** The tool normalizes every weight set to meet this rule.
  Section 6.5 gives the method. The tool reports every weight that it changed.
- **The bounds apply after normalization.** Every stored weight is at least 1
  and at most 100.
- A normalized weight must not fall to 0. The tool refuses such a command and
  it names the holding. The command `set-weight NVDA 99` on a basket of three
  holdings leaves 1 percent for two holdings, so one of them would fall to 0.
  The user must remove that holding instead.
- A basket holds at most 100 holdings.
- An empty basket has no weights, so the invariant does not apply to it.
  `plan-buy`, `plan-sell`, and `show` exit 1 on an empty basket. The message
  must tell the user to add a holding.
- A sell must not exceed the shares held. `record-fills` skips that one order
  and records the rest of the batch. Section 6.2 gives the batch rule and the
  `--cap-at-held` option.
- A duplicate `order_id` in the same basket is not an error. The tool makes no
  change. It exits with code 0 and reports `already_recorded`.
- The same `order_id` in a different basket is an error. The tool exits with
  code 1 and names the basket that holds it.
- The tool converts each symbol to upper case.
- The tool generates the slug from the name at creation. **The slug never
  changes afterward.** `set-name` changes the display name only. A rename must
  not change the slug, because every event refers to the basket by slug.
- A slug must be unique among the baskets that exist. After a `basket_deleted`
  event, the slug is free again. A new `basket_created` event with that slug
  starts a new basket. **The replay assigns each event to the basket that the
  latest `basket_created` before it started.** The old events stay in the log
  and stay with the old basket. `history` shows every lifetime of a slug.
- `remove-holding` refuses to remove a holding that still holds shares. The
  tool reports the share count. The `--force` option appends a
  `holding_removed` event. The earlier events stay in the log. The user must
  sell the shares first, or accept that the basket no longer tracks them.
- `delete` refuses to delete a basket that holds any shares. The tool reports
  the total value at cost. The `--force` option appends a `basket_deleted`
  event and removes the snapshot file. Deletion removes the record only. It
  does not sell any stock. The agent must state this fact to the user before
  it calls the command.

### 6.8 Write method

Each write command performs four steps in this order.

1. It takes an exclusive `flock` on `~/.tradethos/events.log.jsonl`.
2. It validates the full command. It replays the log for this check. A
   rejected command appends no event.
3. It appends the new events. It opens the file in append mode. It writes each
   event as one line. It then calls `flush` and `os.fsync`.
4. It releases the lock. It then writes a new snapshot for each basket that
   the events changed. It writes each snapshot to a temporary file in the
   same directory and then calls `os.replace`.

Steps 1 to 3 must hold the lock together. The duplicate `order_id` check reads
the log and then writes to it. Without a lock, two sessions can both read,
both find no duplicate, and both write. The lock removes that race.

Step 3 makes the change durable. Step 4 can fail without loss. No command
reads a snapshot, so a stale or missing snapshot has no effect. The next
write, or the `export` command, writes it again.

The tool must not depend on atomic appends. A `flock` gives the guarantee. The
replay rule in section 5.7 gives a second guarantee, because it ignores a
repeated `order_id`. The design needs both. A lock protects one machine. The
replay rule repairs a log that already holds a duplicate.

---

## 7. Data flow

### 7.1 Buy an amount of a basket

The user says "buy 100 dollars of basket A".

1. The agent calls `plan-buy <slug> --amount 100`. The tool returns the dollar
   amount for each symbol.
2. The agent calls `get_equity_tradability` and `get_equity_quotes` for every
   symbol in the plan.
3. The agent calls `review_equity_order` for each symbol. It shows the full
   plan to the user as one table.
4. The user confirms the plan once. The user does not confirm each order.
5. The agent calls `place_equity_order` for each symbol. It uses a new
   `ref_id` for each order. **It keeps the order identifier that each call
   returns.**
6. The agent calls `get_equity_orders` to read the results.
7. The agent passes that response to `record-fills <slug>`. It also passes the
   identifiers from step 5 as `--order-ids`, and the account as `--account`.
8. The tool records only those orders. It appends one event for each filled
   order and writes a new snapshot.
9. The tool reports the new positions. It also reports every identifier that
   it did not record, with the reason.
10. The agent reports the result to the user.

Step 5 needs care. The agent must keep the identifiers. Section 6.2 gives the
reason. Without them, the tool cannot tell a basket order from an order that
the user placed for another purpose.

Step 6 can run too early. A limit order can stay open for hours or days. The
agent must then run steps 6 to 9 again later, with the same `--order-ids`
list. The idempotency rule makes a repeated call safe, because the tool skips
every order that the log already holds. This repeat is the intended way to
record a late fill.

Step 4 also needs care. The user confirms one plan that contains many orders.
The agent must show the total cost and every symbol before it asks for
confirmation. The rules in `AGENTS.md` still apply to each order.

### 7.2 Sell an amount of a basket

The user says "sell 100 dollars of basket A" or "sell all of basket A".

1. The agent calls `get_equity_quotes` for every symbol in the basket.
2. The agent calls `plan-sell <slug> --amount 100 --prices '{...}'`, or
   `plan-sell <slug> --all`. The tool returns a share count for each holding.
3. The agent shows the plan and the estimated proceeds as one table.
4. The user confirms the plan once.
5. The agent places one sell order for each holding in the plan. It keeps
   every order identifier.
6. The agent calls `get_equity_orders`.
7. The agent calls `record-fills` with the response, the identifiers, and the
   account. The tool reads the `side` of each order and appends `sell`
   events.
8. The tool reports the new positions and the realized profit or loss.

The agent must state one fact before step 4. A sale reduces a real position.
It is not a change to a record.

### 7.3 Buy or sell a single stock for a basket

This is the research-first journey. The user researches a stock, adds it to a
basket, and buys it. The user can also sell one holding of a basket.

1. When another basket already holds the stock, the agent states the rule of
   section 5.9: one order funds one basket only.
2. The agent follows the normal order steps in the trade-executor skill. It
   keeps the order identifier.
3. The agent calls `record-fills <slug>` with the order response, that one
   identifier, and the account.
4. The tool records the trade and reports the new position.

When the user named the basket in the request, step 3 is part of the request.
When the user did not name a basket and a basket holds the stock, the agent
offers the record. It does not write it without a yes. The Skill Coordination
rules in `AGENTS.md` state this framing.

**The agent must not offer the record for a purchase that closes a gap from
section 7.4.** The basket already claims those shares. A record would count
them twice.

### 7.4 Repair a basket after an outside sale

A basket claims a number of shares. The user can sell those shares outside the
basket. The account then holds fewer shares than the basket claims. The basket
reports a position that the user no longer owns.

The tool finds this state. Check 3 of `verify` compares the claims of every
basket to the account position. The portfolio-tracker skill runs this check
when it reports a basket.

The repair records the outside sale into the basket. The record then follows
the real trade at the real price.

**The user names the basket.** The tool cannot know which basket a sale came
from. A sale order has no basket tag, and the intent exists only in the
user's head. When one basket claims the symbol, the choice is clear. When two
or more baskets claim the symbol, the agent shows each basket's claim next to
the account position, and the user picks the basket.

1. The agent finds the sale order in `get_equity_orders`.
2. The agent shows the order to the user: the symbol, the share count, the
   `average_price`, and the date.
3. The user confirms that this order sold the basket's shares.
4. The agent calls `record-fills` with that order identifier. The basket then
   claims fewer shares. The realized profit or loss is exact, because the
   price came from the order.

The sale order can hold more shares than the basket claims. The user can sell
basket shares and outside shares in one order. The live NVDA position shows
the case: the account held 0.116726 shares and the basket claimed 0.068659.
A sale of the full position exceeds the basket's claim, so `record-fills`
skips it by default. The agent then uses `--cap-at-held`. The tool records a
sell of the 0.068659 shares that the basket holds, at the order's
`average_price`. The agent must state the capped share count before the call.

**One sale order can repair one basket only.** One `order_id` funds one
basket, and the replay ignores a second event with the same identifier. A
sale that took shares from two overlapping baskets in one order can therefore
be recorded into only one of them. The other basket keeps its warning. The
user then buys those shares again, or accepts the wrong record. Section 12
records this limit.

The user can refuse the repair. The warning then stays, and the basket
reports a position that the account does not hold. The agent must state this
fact every time it reports that basket.

The user can also buy the shares again instead. The account then matches the
claims. The agent must not record that purchase, because the basket already
claims those shares. The agent must state one fact before this choice: the
order spends money.

### 7.5 Change the weights of a basket

A weight change moves money between holdings. The total must stay at 100
percent, so a change to one holding always changes another. **The agent must
never write a weight change without a decision from the user.**

The user says "raise NVDA to 20 percent".

1. The agent calls `set-weight <slug> --symbol NVDA --weight 20 --dry-run`.
   The tool returns the complete weight set that it would write.
2. The agent shows every holding, its old weight, and its new weight. It shows
   the holdings that the user did not name.
3. The agent states the fill mode that it used. It offers the other mode.
4. The user confirms the set, or gives different numbers.
5. The agent calls `set-weights` with the confirmed numbers. This command
   writes the exact set that the user approved.

**When the user gives different numbers, the agent returns to step 1.** It
calls `set-weights --dry-run` with those numbers. The tool normalizes them,
because a set from a user rarely sums to exactly 100. The agent shows the
normalized set and asks again. The loop ends when the user approves a set that
the tool did not change.

This loop keeps the promise of step 5. The agent writes only a set that it
has already shown to the user in its final form.

Step 5 uses `set-weights` and not `set-weight`. The user approved a complete
set, so the agent writes that complete set. The tool then performs no
arithmetic of its own, and the written weights match the weights that the user
saw.

The same five steps apply to `add-holding` and `remove-holding`. Both commands
change the weights of holdings that the user did not name.

A weight change is not a trade. It changes the target only. The agent must
state this fact. The basket then holds a drift against the new targets, and
`calc_drift.py` reports it.

### 7.6 What this replaces

Step 7 of the purchase journey replaces five steps in the current design. The
current design fetches the watchlist, encodes new metadata, calls
`update_watchlist`, fetches the watchlist again to verify, and runs a
recovery path on failure.

---

## 8. Migration

### 8.1 Sources

The migration reads three sources. Each source holds data the others lost.

| Source | Contribution |
|---|---|
| Cloud `Z64:` metadata | Target weights, threshold, current shares, average cost |
| Legacy `data/baskets/*.json` | Name, description, thesis for each holding, creation date |
| `get_equity_orders` | Transaction dates, fill prices, order identifiers |

The legacy local files still hold the thesis text for each holding. The cloud
format never stored that text. The migration must read these files.

### 8.2 Attribution algorithm

An order does not record its basket. The migration must assign each order.

1. If a symbol belongs to one basket, assign all its filled orders to that
   basket.
2. If a symbol belongs to more than one basket, mark it as ambiguous. The
   user resolves it. In the current data, `NVDA`, `MU`, and `LITE` are
   ambiguous.
3. For each symbol in a basket, read the orders from oldest to newest. Add the
   shares until the total matches the share count in the `Z64:` snapshot.
   Matched orders become the log. Report the remaining orders.

The `Z64:` snapshot stores shares with 5 decimal places. The orders store
shares with 6 decimal places. An exact comparison therefore fails. The
migration must treat the total as a match when the difference is below
0.000005 shares. The migration must report any symbol that it cannot match
within that tolerance.

The NVDA data shows how this works. The Magnificent 7 `Z64:` snapshot claims
0.06865 shares. The order history holds 0.068659 shares from 23 July and 0.048067
shares from 27 July. The first order matches the snapshot. The migration
reports the second order as unassigned. This is correct. The user bought those
shares outside the basket.

The migration also uses time as supporting evidence. All seven Magnificent 7
orders filled within 12 seconds. A basket purchase is a burst of orders. The
migration must not use time as the only reason to assign an order.

### 8.3 Safety rules

- `--dry-run` is the default. The migration prints its plan. It writes
  nothing. The user must pass `--apply` to write.
- The migration must not change or delete any watchlist. The cloud data stays
  as it is. The user can delete the watchlists later.
- The migration runs `verify` at the end. It prints a table. The table
  compares the `Z64:` snapshot shares, the replayed shares, and the account
  position for each symbol.
- The migration reports every unresolved order. It does not guess.
- The migration normalizes the target weights of every basket. The current
  baskets do not meet the invariant in section 6.5. The Magnificent 7 basket
  holds seven weights of 14.29 percent. The migration writes 15 percent for
  `AAPL` and `AMZN`, and 14 percent for the other five holdings. It reports
  every weight that it changed.

### 8.4 Reuse

The migration uses `reconstruct_basket_positions` as its replay engine. The
v0.2.2 release fixed and tested this function. The function stops being live
code. It becomes a one-time tool.

The migration must write through `basket_events.py`. It must not write
`events.log.jsonl` itself. Section 6 states that only one code path writes to
the store, and the migration is not an exception. This rule gives the
migration the same lock, the same validation, and the same event format as
every other write. A migration defect then cannot produce a log that the
normal tool refuses to read.

---

## 9. Error handling

### 9.1 The safety property

The log is append-only and authoritative. Every read replays it. The snapshot
files hold no unique data. This property removes the risk of data loss from a
failed snapshot write.

One file now holds all history. The user must therefore back up
`events.log.jsonl`. The `backup` command copies it, and the tool runs that
command by itself. Section 12 records this risk.

### 9.2 Concurrent writes

Two agent sessions can run on one machine. The design must therefore handle a
concurrent write. It uses two mechanisms together.

The first mechanism is an exclusive `flock` on the log. A write command holds
the lock while it validates and appends. Section 6.8 gives the steps.

The lock is necessary. The duplicate `order_id` check reads the log and then
writes to it. Two sessions can otherwise both read, both find no duplicate,
and both append the same order. That result is the double-count defect that
the check exists to prevent.

An earlier version of this design claimed that a lock was unnecessary. It
argued that a POSIX append below `PIPE_BUF` is atomic. That argument was
wrong for two reasons. The `PIPE_BUF` guarantee covers a pipe and a FIFO, not
a regular file. Buffered output in Python can also split one line into two
writes. An atomic append would not fix the read-then-write race in any case.

The second mechanism is the replay rule in section 5.7. The replay ignores a
repeated `order_id`. A duplicate line therefore changes no position.

The two mechanisms cover different cases. The lock prevents a duplicate on one
machine. The replay rule repairs a log that already holds a duplicate, from
any cause.

### 9.3 Failure table

| Failure | Response |
|---|---|
| The basket does not exist | Exit 1. List the available slugs. |
| The basket is empty | Exit 1. Tell the user to add a holding. |
| A weight is negative or is not a number | Exit 1. Refuse the command. |
| A result would give a holding 0 percent | Exit 1. Name that holding. |
| The sell exceeds the shares held | `record-fills` skips that order and records the rest. `--cap-at-held` records the held shares. See section 6.2. |
| The `order_id` exists in this basket | Exit 0. Change nothing. Report `already_recorded`. |
| The `order_id` exists in another basket | Exit 1. Name that basket. |
| A snapshot file is missing or corrupt | No effect on any read. The next write or `export` rewrites it. |
| A log line is corrupt | Report the line number. Refuse to replay that basket. |

A corrupt log line is the one failure the tool cannot repair. The tool must
not guess the missing values. It reports the line number and the basket. The
user then edits that line or removes it. A corrupt line must not stop the
replay of any other basket.

### 9.4 Error output

The tool prints errors as JSON on stderr.

```json
{"error": "Order 6a626aa2 already funds basket storage-leaders",
 "code": "ORDER_IN_OTHER_BASKET",
 "detail": {"order_id": "REDACTEDA1-0000-0000-0000-000000000001",
            "basket": "storage-leaders"}}
```

Exit codes separate the failure types. Code 1 is a validation error. Code 2 is
an input or output error. Code 3 is an integrity error.

### 9.5 Event schema versions

Each event carries a `v` field. The log keeps every line forever. A line
written today must stay readable in five years.

The reader must therefore follow three rules.

1. The reader must accept every version that the project has released.
2. A new field must have a default value. An old line then stays valid.
3. The project must not change the meaning of an existing field. A new meaning
   needs a new field name and a new version number.

The reader upgrades an old event to the current shape in memory. It never
rewrites the log.

---

## 10. Testing

`basket_store` takes the data directory as a parameter. The default is
`~/.tradethos`. Tests pass a temporary directory. No test touches real user
data. This parameter is internal. The user does not configure it.

Unit tests must cover the money math. Every defect in the v0.2.2 review came
from this code.

- The average cost across two or more buys.
- A partial sell.
- A sell of all shares.
- The realized profit and loss.
- The skip of an oversell inside a batch.
- The no-op result for a duplicate `order_id`. A repeated call must not change
  the position.
- The refusal of an `order_id` that another basket holds.

Tests must cover the replay logic.

- A replay of an empty log gives no baskets.
- A replay produces the same result each time it runs.
- A `basket_deleted` event removes the basket from every read. The events
  before it stay in the log.
- A `weight_changed` event changes the target weight and nothing else.
- A reader accepts an event that holds an older `v` value.
- A corrupt log line stops the replay of its basket. It must not stop the
  replay of any other basket.
- Deleting every snapshot file must not change the output of any read
  command. `export` must restore the files.
- After a `delete`, `create` with the same name must start a new empty
  basket. The old events must stay with the old basket. `history` must show
  both lifetimes.

Tests must cover `record-fills`.

- It reads a real `get_equity_orders` response. It must read `average_price`
  and not `price`.
- **It must record only the orders in `--order-ids`.** Give it a response
  that holds one basket order and one unrelated order for the same symbol.
  The unrelated order must not reach the log.
- It must record a single order. One identifier in `--order-ids` must record
  one trade.
- It must refuse an account that differs from the basket account.
- It must skip an order that the log already holds. A second call with the
  same response must not change any position.
- **It must read the `side` of each order.** Give it a response that holds
  one buy order and one sell order. The tool must append one `buy` event and
  one `sell` event. The realized profit must be correct.
- It must record a late fill. Call it once while an order is open, and again
  after the order fills. The second call must add the position.
- **It must record a mixed batch.** Give it seven orders. Make one a sell
  that exceeds the shares held. The tool must record the other six. It must
  report the failed order with a reason. It must exit with code 0.
- It must exit with code 1 when it records no order.
- **`--cap-at-held` must record a sell of the held shares.** Give it a sell
  order for more shares than the basket holds. Without the option, the tool
  must skip the order. With the option, it must record a sell of the held
  share count at the order's `average_price`.
- `--cap-at-held` must be refused when `--order-ids` holds more than one
  identifier.

Tests must cover the planning commands.

- `plan-buy` divides an amount by the target weights. Test a weight set that
  does not divide evenly. The allocated amounts must sum to the full amount,
  because the weights always sum to 100.
- `plan-sell --all` must return the full share count of every holding. With
  `--prices` it must also return the estimated proceeds.
- `plan-sell --amount` must divide by the current market value. Test a basket
  whose current weights differ from its target weights. The share counts must
  keep the current weights unchanged.
- `plan-sell --amount` must refuse an amount above the basket value.
- Both planning commands must refuse an empty basket.

Tests must cover the weight invariant.

- **Every command must leave the weights at exactly 100.** Test `create`,
  `set-weight`, `set-weights`, `add-holding`, and `remove-holding`. Sum the
  weights after each one.
- `create` with seven equal holdings must give 15 to `AAPL` and `AMZN`, and 14
  to the other five. The extra percent must go to the holdings that are first
  in alphabetical order.
- `create` with the ratio `NVDA:2,MSFT:1` must give 67 and 33.
- A ratio input must normalize. `NVDA:200,MSFT:100` must give 67 and 33.
- `create` must write no `weight_changed` event. Each `holding_added` event
  must carry the normalized weight.
- `set-weight` must scale the other holdings and report every changed weight.
- `set-weights` must refuse a list that omits a holding of the basket.
- `set-weights` must refuse a symbol that the basket does not hold.
- `set-weights` must append one `weight_changed` event for each holding that
  changed, and none for a holding that did not change.
- `--dry-run` must append no event and change no file. Run it, then confirm
  that the event count did not change.
- `--fill proportional` must keep the ratios of the other holdings. Test the
  example in section 6.5. NVDA at 20 must give MSFT 48 and AAPL 32.
- `--fill equal` on the same basket must give MSFT 40 and AAPL 40.
- `--fill proportional` must be the default.
- The tool must refuse a command whose result gives a holding 0 percent. Test
  `set-weight NVDA 99` on a basket of three holdings. The message must name
  the holding.
- Every stored weight must be a whole number after every command.
- The tool must refuse a negative weight and a weight of zero.
- `add-holding` must scale the other holdings down. `remove-holding` must
  scale them up.
- Normalization must be repeatable. Running `set-weights` twice with the same
  input must produce no second event.
- `remove-holding` on the last holding must leave an empty basket without an
  error.

Tests must cover the concurrency rules and the integrity rules.

- A replay must ignore a second event that repeats an `order_id`. Write the
  duplicate line directly into the log for this test. The position must not
  change.
- A replay must report every event that it ignores. Test a duplicate
  `order_id` in a second basket. The report must name both baskets.
- Two concurrent write commands must not both append the same `order_id`. Run
  two processes against one temporary store.
- `set-name` must not change the slug.
- `basket.py` must run a backup by itself after enough writes.
- An automatic backup must hold the write lock.
- `verify` must warn when `backup.marker` is absent or older than the log.
- Check 3 of `verify` must report the three states of section 6.6. Test a
  basket that claims more shares than the account holds. Test a basket that
  claims fewer shares. The second case must not raise a warning.

Command-line tests run each subcommand as a subprocess. They check the exit
code and the output. This method found a defect in `main()` during the v0.2.2
work. Unit tests alone did not find it.

Migration tests use the real MCP payloads already captured in the test suite.
These fixtures record the true response shapes. They transfer directly to the
migration tests.

The existing CI workflow needs no change. It runs the suite on Python 3.9 to
3.13.

---

## 11. Scope of changes

### 11.1 Scripts

| File | Action |
|---|---|
| `basket_utils.py` | Delete. Move its contents into `migrate_v2.py`. |
| `basket_events.py` | Create. Append events under a lock. Read and upgrade old versions. |
| `basket_store.py` | Create. Replay the log. Validate. |
| `basket.py` | Create. The command-line tool. |
| `basket_summary.py` | Delete. `basket.py list` replaces it. |
| `list_symbols.py` | Delete. `basket.py list` replaces it. |
| `calc_performance.py` | Keep. Read the local store. Remove `--watchlists-json`. |
| `calc_drift.py` | Keep. Read the local store. Remove `--watchlists-json`. |
| `migrate_to_watchlists.py` | Delete. It migrates in the wrong direction. |
| `migrate_v2.py` | Create. The one-time migration. |

### 11.2 Skills

| Skill | Change |
|---|---|
| `basket-manager` | Large rewrite. Storage, every command, and the script section. State the agent rule from section 6.2: pass only the identifiers of orders placed for the basket. |
| `trade-executor` | Rewrite the "Recording Basket Transactions" section. A fill joins a basket through `record-fills` with the order id. The agent offers the record when the user did not name a basket. |
| `portfolio-tracker` | Read from `basket.py show`. Run check 3 of `verify` when it reports a basket. Offer the repair in section 7.4. Remove the recovery guidance. |
| `stock-researcher` | Small change to the cross-skill offer. |
| `stock-screener` | Small change to the cross-skill offer. |

### 11.3 Documents

- `AGENTS.md`: rewrite the "Custom Baskets" section. Remove the claim that the
  format holds 30 or more symbols. Update the "Skill Coordination" rule: the
  offer to record a fill stays, and the record goes through `record-fills`
  with the order id.
- `README.md`: rewrite the architecture section and the feature list.

### 11.4 Script paths

The skills must call `basket.py` on almost every basket operation. The current
skill files use paths relative to the repository. Those paths fail for an
installed plugin. This work must fix the paths. Use `${CLAUDE_PLUGIN_ROOT}`.

### 11.5 Removed dependencies

The basket-manager skill stops using five MCP tools: `create_watchlist`,
`update_watchlist`, `get_watchlists`, `add_to_watchlist`, and
`remove_from_watchlist`. Basket operations no longer need the network.

---

## 12. Risks

**This is the second storage migration in three versions.** Version 0.1 used
local files. Version 0.2 moved to the cloud. Version 0.3 returns to local
files.

The v0.2 move used a capacity claim of 30 or more symbols. Measurement
disproved that claim. This design uses measured numbers. The requirements also
need a sub-portfolio ledger. A 256-character field cannot hold that ledger.
A third reversal would be expensive.

**The design loses cross-device support.** The user accepted this cost. A
second machine shows no baskets until the user copies the directory. The
`~/.tradethos/` location makes that copy simple.

**A position enters a basket only through a recorded order.** Shares that the
user already holds cannot join a basket, because no order maps them to it. A
transfer from another broker has the same limit. This design accepts the
loss. The cost basis of every basket position then comes from a Robinhood
order, and no command takes a typed number. Section 14 records the gap. A
future version can add an import command that reads a holding from
`get_equity_positions`, with its own guards.

**The basket records can drift from reality.** The log holds a trade only
when the user records it. A sale outside a basket makes the records wrong
until the user repairs them. `verify` finds that state. The repair in section
7.4 records the sale order into the basket, so the repaired record keeps the
exact price. The risk that remains is a user who ignores the warning. The
agent must repeat the warning on every report of that basket.

**The `--order-ids` list is a claim that the tool cannot verify.** A wrong
identifier assigns a real order to the wrong basket. Three guards bound this
risk: the cross-basket `order_id` check, the account check, and check 3 of
`verify` against the real positions. The agent-side rule in section 6.2
covers the rest.

**One sale order can repair one basket only.** A sale that took shares from
two overlapping baskets in one order can be recorded into only one of them.
Section 7.4 gives the fallback for the other basket. This case needs an
overlap on one symbol and one order that spans both claims, so it is rare. A
future version can relax the rule: the shares recorded against one order,
summed across baskets, must not exceed the order's filled shares.

**The migration depends on correct attribution.** The algorithm reports every
case it cannot resolve. The user must review the report. A wrong assignment
produces a wrong cost basis.

**One file now holds all history.** The event log is the only file that the
tool cannot rebuild. The loss of that file loses every basket definition and
every trade record.

Three facts reduce this risk. The tool only appends to the file, so a write
touches the end of the file. Robinhood keeps the order history, so the user
can rebuild the trades. The snapshot files keep a readable copy of the
current state.

One fact remains. Only the log records which basket owns an order. The tool
therefore backs the log up by itself, and `verify` warns when the backup is
stale. Section 6.4 gives both rules.

---

## 13. Implementation order

This design covers more than one implementation plan. Build it in three
stages. Each stage must pass its tests before the next stage starts.

1. **The store and the tool.** Build `basket_events.py`, `basket_store.py`,
   and `basket.py`. Write the unit tests, the concurrency tests, and the
   command-line tests. This stage touches no skill and no live data.
2. **The migration.** Build `migrate_v2.py`. Test it against the captured MCP
   payloads. Run it with `--dry-run` against the live account. Review the
   report. Then run it with `--apply`.
3. **The skills and documents.** Update the five skill files, `AGENTS.md`, and
   `README.md`. Fix the script paths. Delete the obsolete scripts.

Stage 1 gives a working tool with no risk to live data. Stage 2 needs stage 1.
Stage 3 needs stage 2, because the skills must describe the migrated state.

---

## 14. Out of scope

- Log rotation and archives.
- Cross-device synchronisation.
- A read API beyond `show`, `history`, and `verify`.
- Any change to the research, screener, or order-execution logic.
- **Existing holdings and transfers.** A position that the user already holds
  has no order that maps it to a basket. A transfer from another broker has
  no order at all. `record-fills` reads only orders, so neither can join a
  basket. A future version can add an import command with its own guards.
- **Rebalancing.** `plan-sell --amount` reduces every holding in proportion to
  its current value. It keeps the current weights. A trade that moves a basket
  toward its target weights is a separate operation. `calc_drift.py` reports
  the gap, and the user then decides. A `plan-rebalance` command is future
  work.
- **Return over time.** The log holds dated cash flows, so a later version can
  compute an annualised return. This version reports the total invested
  amount, the current value, and the profit or loss.
