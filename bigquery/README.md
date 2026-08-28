# The pipeline

One question: how much block space changed hands below the public price, in
blocks where space was actually scarce?

Everything runs from `run_pipeline.py`, one month per run. Step 01 reads that
month's partition of the public dataset (~29 GB); every later step works on
local tables.

```
01_tx_base       one pass over crypto_bitcoin.transactions
02_blocks        block -> mining pool, plus an empty floor_fee_rate column
03_txs           in-block CPFP edges, and the non-relayable reasons
04a_in_package   the subset that union-find has to look at
04b union-find   Python: packages priced as sum(fee) / sum(vbytes)
04c_update       package rates written back onto txs
04d_ancestors    the one relay rule that needs the CPFP graph
05_block_floor   p05 of the effective rates in each block
05b_update       floor = median of the p05 of b-3..b-1, b+1..b+3
06a_fullness     which blocks were full, and had full neighbours
06b_low_fee      low-fee test at 0.3 / 0.5 / 0.7 of the floor
07_revenue       the two value bands per low-fee transaction
07b_monthly      the monthly answer
07c_pool         the same answer per pool
08_sensitivity   the 3x3 threshold grid
```

## The four decisions that shape the answer

**Price belongs to the package, not the transaction.** A child that pays for
its parent means the two moved at one price. Fee rates are therefore computed
after grouping, never before: an in-block parent/child relation is an
undirected edge, connected components are packages, and every member takes
`sum(fee) / sum(vbytes)`. Without this, every ordinary CPFP parent looks like a
gift. This is the one step that leaves SQL — `unionfind.py` and
`effective_fee.py` — and the one step with its own tests.

**A block is judged by its neighbours, never by itself.** The floor of block b
is the median of the p05 effective rate of b-3, b-2, b-1, b+1, b+2, b+3. A pool
that stuffs its own block with cheap transactions drags down its own p05; it
cannot drag down its neighbours'. All six neighbours must have a value or the
floor is NULL and the block takes no part in the low-fee test.

**Non-relayable traffic is excluded, not counted.** A transaction that a
default node of its day would refuse to relay never entered the public auction
at all, so its low price is explained by policy, not by a private deal.

Each test uses the rule in force on the day of the block and no other. When a
release loosened a rule, what the old rule would have caught counts as
relayable from the release date on, even though most of the network had not
upgraded yet: a 0.4 sat/vB transaction in October 2025 is legal traffic here,
not evidence. When a release tightened one, the new rule starts on its release
date. A case the rule in force does not settle is not counted at all.

| reason | rule | gate |
|---|---|---|
| `nonrelay_nonstandard_script` | an output scriptPubKey matching no standard template | none |
| `nonrelay_bare_multisig` | more than 3 pubkeys | none |
| `nonrelay_op_return` | OP_RETURN scriptPubKey over 83 bytes, then over 100,000 summed across the outputs | 2025-10-08, Core v30 |
| `nonrelay_multi_op_return` | more than one OP_RETURN output | until 2025-10-08, Core v30 |
| `nonrelay_dust` | an output worth less than the input that would spend it | one is allowed from 2025-04-15 (Core 29) on a 0-fee parent whose child spends it |
| `nonrelay_version` | version outside 1..2, then outside 1..3 | 2024-10-04, Core 28 |
| `nonrelay_truc` | the version 3 size and version-mixing rules (10,000 vB, 1,000 vB for a child) | from 2024-10-04, Core 28 |
| `nonrelay_sub_minrelay` | under 1 sat/vB, then under 0.1 | 2025-09-04, Core 29.1 |
| `nonrelay_oversized` | virtual size over 100,000 vB (the 400k WU standard limit) | none |
| `nonrelay_undersized` | under 65 non-witness bytes | none |
| `nonrelay_scriptsig_size` | an input scriptSig over 1,650 bytes | none |
| `nonrelay_scriptsig_nonpush` | an input scriptSig that opens with a real opcode | none |
| `nonrelay_ancestor_limit` | more than 25 in-block ancestors, or more than 101,000 vB of them | until 2026-04-20, Core 31 |

The fee-rate test carries one carve-out: a sub-minimum parent with a paying
child in the same block is ordinary CPFP, not a private deal, and after Core 28
(2024-10-04) 1p1c package relay propagates it publicly. Only a sub-minimum
transaction with no paying in-block child is unambiguously non-relayable, so
that is what `nonrelay_sub_minrelay` records.

Two rules are deliberately read as ancestors rather than descendants. A parent
with 40 children in one block broke the descendant limit, and a TRUC parent
with two children broke the TRUC one, but nothing in the data says which child
was the one too many. The transaction over an *ancestor* limit is the one that
was refused, so that is the only side counted. Core 31 replaced both limits
with a cluster limit, which is a property of the whole connected component and
names no single transaction, so `nonrelay_ancestor_limit` never fires after
2026-04-20.

Some rules cannot be tested from this dataset and are missing on purpose. The
witness of an input is not in `crypto_bitcoin`, so the P2WSH and tapscript
stack limits and the taproot annex rule cannot be checked. The scriptPubKey of
a spent output is not there either, so `AreInputsStandard` — spending a
non-standard or unknown-witness-version output, and the P2SH sigop limit —
cannot be checked. Sigop cost is not computed. `nonrelay_scriptsig_nonpush` only
catches a scriptSig whose *first* opcode is not a push, because a full
push-only walk needs a script parser rather than a regular expression.
Everywhere hex alone cannot settle a question, the classifier calls the output
standard: a missed reason understates non-relayable traffic, while a false one
would delete a real transaction from the measurement.

**A discount only counts where space was scarce.** In a block with room to
spare, a cheap transaction costs nobody anything. A block counts as full at
3,900,000 WU with at least 4 of its 6 neighbours also that full — the neighbour
condition is what separates sustained demand from one busy minute.

## Assumptions the spec left open

- The floor percentile runs over **relayable** transactions only. Leaving
  non-relayable traffic in would let the traffic under investigation set the
  price it is measured against. `block_percentiles.p05_all` keeps the
  unfiltered value so the difference stays visible.
- Percentiles are per transaction, not weighted by vbytes.
- The fullness thresholds (3.9M WU, 4 of 6 neighbours) are choices, not facts.
  `08_sensitivity` varies them.
- Pool attribution reads the coinbase tag first and the payout address second.
  Tags are matched case-insensitively. The tags and addresses come only from
  `pools_known.json`, which `refresh_pools.py` downloads from the public
  mempool.space list; the file is committed, so the table behind a published
  number is pinned in git. A pool absent from that list lands in `Unknown`.

## Cost

A run covers one month. End to end it scans about 29 GB, near $0.18 at
on-demand pricing. Step 01 is the only step that reads the public dataset;
every later step reads the local tables the run just built.

`tx_base` and `txs` are partitioned by month and clustered by block number;
`blocks` and the summary tables are small enough to need neither.

Every run prints what each step scanned. `--dry-run` prints it without running
anything, and the full run asks before the one expensive step unless `--yes`
is given.

## Running one month at a time

The source dataset is partitioned by month and the pipeline aggregates by
month, so the normal way to run this is one month per invocation:

```bash
uv run python run_pipeline.py --month 2023-04
uv run python delete_dataset.py
```

`--month` runs the full step list for that month, then merges it into the
JSON files in `out/`. The BigQuery working dataset itself is left in place so
you can inspect it; once the local files hold what you need,
`delete_dataset.py` drops it so storage does not grow across months. Local
output stays on the order of megabytes per month.

## The output files

`out/` is tracked in git, so the answer grows with the repository rather than
being rebuilt from scratch each time. Every table is a JSON array of records:

    monthly_summary.json      per month: low-fee space, share, value bands
    pool_summary.json         the same per pool
    low_fee_sensitivity.json  the 3x3 threshold grid, per month
    low_fee_txs_sample.json   the 5,000 largest low-fee transactions
    headline.json             the numbers quoted in the write-up
    summary.md                a readable digest of all of the above

The merge is keyed on `block_month`. `export_results.py` reads the months the
working dataset holds, drops exactly those months from each file, and writes
the fresh rows in their place. Months the run did not touch are untouched, so
a run only ever adds to the history — and re-running a month you already have
replaces it instead of double-counting it. That is why step 08 groups the
sensitivity grid by month too: its cells are sums, and they are summed across
months only when `summary.md` and `headline.json` are written.

`headline.json` and `summary.md` are derived files. They are rewritten from
the full merged tables on every export, so they always cover every month on
disk. `export_results.py --replace` ignores what is on disk and writes only
what the working dataset holds; use it to rebuild the files from a full-window
dataset.

## The accelerations dataset

`fetch_accelerations.py` and `run_accelerations.py` are a second, separate
pipeline: they crawl the mempool.space acceleration history, not
`crypto_bitcoin`, and write into their own dataset (`accelerations` by
default, `BQ_ACCEL_DATASET`) instead of the working dataset above. That
history is slow to (re)fetch and worth keeping, so `delete_dataset.py` — which
only ever touches `BQ_DATASET` — cannot take it out between months.

The three steps are separate on purpose. **Fetch** reads the API and stores
records, forming no opinion about months. **Aggregate** totals them per month
in BigQuery. **Export** decides which months are finished and writes the file
the write-up quotes.

```bash
uv run python fetch_accelerations.py            # top up with what is new
uv run python run_accelerations.py              # aggregate per month and per pool
uv run python export_accelerations.py           # write ../data/*.json
```

The steady state is those three, and the first one reads a page or two. The
rest of this section is about the one-off: building the history in the first
place.

### The invariant: one contiguous run

The table always holds **one contiguous run** of the history, from some oldest
record up to the newest. Nothing is missing between them. Every fetch mode
extends that run, and none can create an island.

That is what makes the rest cheap. "Which stretch of history do we hold?" is:

```sql
SELECT MIN(added), MAX(added) FROM accelerations
```

and "is this month finished?" is that span covering the month. The data
answers both. There is no ledger of what was fetched, because a ledger would
be a second source of truth, free to drift from the rows it describes.

Contiguity is enforced, not assumed. **A top-up refuses to stop until it has
read a record it already has.** Touching the existing run is what proves the
new records sit directly on top of it rather than floating above a gap;
stopping on a timestamp alone would only prove the walk went far enough back
in time, which is not the same claim. If a top-up reaches the end of the
history without ever touching the run, it loads nothing and says so.

This is also why there is no "fetch me an arbitrary date range". That would
land a disconnected island, `MIN`/`MAX` would then span records nobody read,
and a half-fetched month would be published as finished. Backfilling is
`--back-to`, anchored to the oldest record already held.

### Building the history

```bash
uv run python fetch_accelerations.py --full                # everything, replaces
uv run python fetch_accelerations.py --back-to 2024-01-01  # extend further back
```

`--full` crawls the whole list and replaces the table, so it is also the
wipe-and-reload — there is no separate delete step. At about 610 pages and one
request every 20 seconds it takes roughly four hours. Pages are cached under
`${CACHE_DIR}/accelerations/`, so an interrupted crawl resumes rather than
starting over.

`--back-to` is the cheaper way to extend an existing run downwards. Walking to
a page far down the list would cost one request per page passed, so instead the
entry page is found by halving the page range — about 10 requests for 600
pages — and the walk starts `--overlap` pages above the anchor so the join
overlaps rather than merely meets.

The seek errs the same safe way the walk does: new records only push records
towards higher page numbers, so a page measured during the seek can only have
become newer, and the walk then starts slightly too high. That costs a page,
never a record.

| what you want | pages read | wall time |
|---|---|---|
| top up after a day | 3 | ~2 min |
| top up after a week | 6 | ~3 min |
| extend back one month | ~10 seek + ~17 | ~9 min |
| `--full` | ~610 | ~4 h |

The pace is deliberately slower than the API forces. It publishes no rate
limit, and at one request a second it pushed back constantly enough that the
backoff, not the sleep, set the real rate — about 5 pages a minute either way.
Asking slowly costs a top-up nothing, because a top-up reads a page or two
whatever the pace.

### What gets stored, and what counts

The fetcher stores only records in a **terminal** state: `completed`,
`completed_provisional`, `failed`. An `accelerating` record is still in flight.
Because loads only append and the key is `(txid, added)`, a record stored
mid-flight would keep that stale status for good and never count as revenue
however it actually ended up. Skipping it costs nothing — a later run reads it
again, settled.

`failed` records are stored but earn nothing, so the question "how many failed,
and were their transactions mined anyway?" stays askable.

Every revenue figure counts `completed` and `completed_provisional` records
that were mined, **cancelled or not**. A cancellation is not a rollback: once a
partner pool has the transaction in a block the payment is owed.

Three more properties make the partial walk safe, all tested in
`tests/test_fetch_accelerations.py`:

- **The list only grows at the top.** Sorted by `added` descending, and `added`
  never changes, so an insertion pushes records towards higher page numbers —
  never towards lower ones, where a downward walk has already been. A page
  number is therefore not a bookmark: half a page of new records moves every
  boundary by half a page.
- **The key is `(txid, added)`, not `txid`.** One transaction can carry more
  than one acceleration request — a retry after a failure — and both are real.
  Because `added` never changes, the same key also collapses a record the API
  returned twice.
- **Only `--full` replaces the table.** Every other mode appends what is
  missing, so a short or interrupted fetch can never shrink it.

All of it rests on the list being append-only, which was checked rather than
assumed: re-fetching a year of history nine days after the first load returned
all 8,265 records with no field changed and none missing, and `x-total-count`
has only ever risen.

### What gets published

`export_accelerations.py` writes `../data/accelerations_monthly.json` — small,
in git, and read by the write-up instead of BigQuery, so a figure can be
reproduced without credentials.

Only finished months go in. The unit of the analysis is a calendar month, so a
month is either whole or it is not evidence: a partial August says nothing
about August, and printing it beside a full July invites a comparison that is
not there.

A month is finished when the run covers all of it. Both awkward months then
fall out of the same arithmetic instead of needing rules of their own — the
newest month holds `MAX(added)` inside it, so it is never finished until a
later month's records arrive, and the oldest holds `MIN(added)` inside it, so
it stays out until `--back-to` reaches past its start:

```
=== 4 complete months ===
month      accels   off-chain BTC  off sat/vB  on sat/vB
2026-04       903          0.1520        93.5        2.0
...
2026-08 is still filling (640 records so far) and is held back until it ends.

=== 2 months below the start of the run ===
  2026-02     190 records so far
  2026-03     480 records so far

To take them in, extend the run past the start of 2026-02:
  uv run python fetch_accelerations.py --back-to 2026-02-01
```

The two reasons need different responses, which is why they are printed apart:
a filling month needs nothing, and a month below the run needs a backfill.

The file carries no generated-at stamp and is rewritten only when the numbers
change, so re-running unchanged data produces no commit. `--check` reports and
writes nothing.


`run_accelerations.py`'s steps live in `sql/accelerations/`, apart from the
numbered `01`-`08` pipeline chain in `sql/`.

## Tests

```bash
uv run python -m pytest tests/ -q
```

Offline, no credentials, no cost. The fixtures build synthetic blocks in a
local SQLite database and drive the real reader and the real algorithm: a
3-deep chain, a fan of three children on one parent, two singletons, two
disjoint chains that must not merge, one child funding two parents, and a
parent confirmed in an earlier block that must be ignored. Every expected fee
rate in `test_effective_fee.py` is computed by hand.

`test_sql_steps.py` covers the SQL. Its rendering tests are offline too: they
check that every `${placeholder}` still resolves, and that the two steps
sharing a formula refer to it rather than retyping it. They read the raw file,
not the rendered SQL, because a retyped copy renders identically to the
shared one.

`test_relay_rules.py` covers the relay rules, which is where a mistake costs
the most: a false reason deletes a relayable transaction from the measurement.
It runs *the pipeline's own SQL*, lifted out of `01_tx_base.sql` and
`03_txs.sql`, over inline fixtures — hand-built scriptPubKeys with
hand-computed dust thresholds, and one transaction on each side of every
policy date. Inline data scans nothing, so the queries are free, but they do
run and so need credentials:

```bash
BQ_FIXTURES=1 uv run python -m pytest tests/test_relay_rules.py -q
```

Its dry-run tests ask BigQuery to parse and plan each step:

```bash
BQ_DRY_RUN=1 uv run python -m pytest tests/test_sql_steps.py -q
```

A dry run scans nothing and is not billed, but it does need credentials, and
it needs the tables an earlier step builds. A step whose input is missing is
skipped; only SQL that BigQuery rejects is a failure. Without the variable
these tests skip, so `pytest tests/` stays free and offline.

## Validation

`sanity_check.py` prints each pool's share of blocks per month. Compare it
against a public hashrate chart. If a share is off by more than about 2 points,
attribution is broken and every per-pool number is worthless. It also lists the
coinbase text of unattributed blocks, which is how a missing tag is found.

`validate_against_mempool.py` samples low-fee blocks and compares them against
mempool.space block audits. `addedTxs` is a presence measure and the low-fee
test is a price measure, so partial overlap is the expected result.
Transactions that
appear in `acceleratedTxs` were bought out of band through a public service:
they confirm the mechanism, and they are the part of the count that was never
invisible.
