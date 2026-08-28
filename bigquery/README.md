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
  Tags are matched case-insensitively; `refresh_pools.py` replaces the built-in
  table with the public mempool.space list.

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

```bash
uv run python fetch_accelerations.py          # top up: read only what is new
uv run python fetch_accelerations.py --full   # re-crawl the whole history
uv run python fetch_accelerations.py --since 2024-01-01 --until 2024-04-01
uv run python run_accelerations.py            # build the summary tables from it
uv run python export_accelerations.py         # write ../data/*.json for the write-up
```

The steady state is one command: a top-up reads the page or two that is new.
The backfill below is the one-off, and `export_accelerations.py --check` says
how much of it is left.

A top-up is the normal run. The history endpoint is newest-first and takes no
`since` parameter, so the walk starts at page 1 and stops once it has read
`--overlap` consecutive pages lying at or before the watermark — by default
`MAX(added)` already in BigQuery, or `--since` to override it. At the recent
rate of about 25 accelerations a day, a daily top-up reads one page and a
month-old one reads about fifteen; a `--full` re-crawl reads about 610.

That difference is what the flag buys. Requests go out at one every 20
seconds — three a minute, `--sleep` to change it — so pages, not seconds, set
the wall time:

| gap since last fetch | pages read | wall time |
|---|---|---|
| nothing new | 2 | ~1.5 min (measured) |
| a day | 3 | ~2 min |
| a week | 6 | ~3 min |
| a month | 17 | ~7 min |
| `--full` | ~610 | ~4 h |

Every walk reads `--overlap` pages beyond the new ones, and the stats call
adds one request, so a top-up with nothing to fetch still costs three
requests.

### Fetching a range

`--until` asks for a slice instead of a top-up: the records added between
`--since` (the start of the history when left out) and `--until`. That four
hour `--full` row is the reason the flag exists — a range can be fetched a
month at a time, in any order, over as many sittings as it takes.

Nothing has to be tracked between those sittings. A range is appended on the
same `(txid, added)` key as a top-up, so a range already loaded costs a re-read
and no rows, and overlapping ranges are safe to ask for. Only `--full` ever
replaces the table.

The entry page is the part that would otherwise be expensive: a range ending a
year back sits ~600 pages down, and walking there costs one request per page
passed. The list is sorted, so the entry page is found by halving the page
range instead — about 10 requests for 600 pages, and the walk starts
`--overlap` pages above what it finds. A three month range a year back
therefore costs about 10 seek requests plus the ~75 pages it actually reads,
not 675.

The seek is safe for the reason the walk is. New records only push records
towards higher page numbers, so a page measured during the seek can only have
become newer by the time the walk reaches it — the walk then starts slightly
too high, which costs a page, never a record.

`--max-pages` counts walked pages only, so `--until X --max-pages 3` reads the
first three pages of the range after seeking to it. That is the cheap way to
check a range holds what you expect before paying for all of it.

The pace is deliberately slower than the API forces. It publishes no rate
limit, and at one request a second it pushed back constantly enough that the
backoff, not the sleep, set the real rate — about 5 pages a minute either way.
Asking slowly costs a top-up nothing, because a top-up reads a page or two
whatever the pace.

Three properties make the partial walk safe, and all three are tested in
`tests/test_fetch_accelerations.py`:

- **The watermark is a comparison, never a lookup.** Nothing has to still
  exist at that timestamp: `added > watermark` selects the records that follow
  it, not the one it came from.
- **The list only grows at the top.** It is sorted by `added` descending and
  `added` never changes, so an insertion pushes records towards higher page
  numbers — never towards lower ones, where a downward walk has already been.
  A page number is therefore not a bookmark: half a page of new records moves
  every boundary by half a page.
- **The key is `(txid, added)`, not `txid`.** One transaction can carry more
  than one acceleration request — a retry after a failure — and both are real.
  Because `added` never changes, the same key also collapses a record the API
  returned twice.

The load is `WRITE_APPEND` of what is missing, so a top-up, a range, or a run
cut short by `--max-pages` can never shrink the table. Only a `--full` crawl
that reached the end of the history replaces it.

All of it rests on the list being append-only, which was checked rather than
assumed: re-fetching a year of history nine days after the first load returned
all 8,265 records with no field changed and none missing, and `x-total-count`
has only ever risen.

### What gets published, and when a month is allowed to count

The unit of the analysis is a calendar month, so a month is either whole or it
is not evidence. A partial August says nothing about August, and printing it
beside a full July invites a comparison that is not there.
`export_accelerations.py` writes `../data/accelerations_monthly.json` — small,
in git, and read by the write-up instead of BigQuery, so a figure can be
reproduced without credentials — and it puts a month in that file only when two
things hold:

- **the month has ended**, so no more records can arrive in it;
- **the month has been read**, so some run actually walked the whole of it.

The second is the one that needs machinery. Nothing in the rows distinguishes a
month half fetched from a genuinely quiet month — both are just fewer records.
So every run that loads also appends the span it read to
`${accel_dst}.acceleration_coverage`, and the exporter merges those spans. A
month is published when one merged span holds all of it.

The first condition then needs no calendar rule of its own. Coverage ends when
the last run *started*, and that instant is inside the current month, never past
its end, so the current month fails the same test and keeps failing until a run
happens in the following month. Both requirements fall out of one piece of
arithmetic, which is what `tests/test_export_accelerations.py` pins down.

The exporter separates the two reasons a month is held back, because they need
different responses:

```
2026-08 is still filling (640 records so far) and is held back until it ends.

=== 2 months with a gap ===
  2026-01     210 records so far  needs 2026-01-01 to 2026-02-01
  2026-02     480 records so far  needs 2026-02-01 to 2026-03-01

The oldest gap first:
  uv run python fetch_accelerations.py --since 2026-01-01 --until 2026-02-01
```

A filling month needs nothing — the next top-up finishes it. A gap needs a
backfill, and the exporter prints the exact range. Work down that list and the
backfill is done; after that, top-ups alone keep the file current.

The file carries no generated-at stamp and is rewritten only when the numbers
change, so a re-run of unchanged data produces no commit.

**One-off, for a table loaded before the ledger existed:**

```bash
uv run python export_accelerations.py --seed-coverage
```

This claims the span between the oldest and newest loaded record as read. It is
an assumption, not a measurement — true if that data came from full crawls and
top-ups, false if a crawl was interrupted in the middle. Anything it gets wrong
is fixed by re-fetching that range, which costs pages and no rows.

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
