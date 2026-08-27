# The pipeline

One question: how much block space changed hands below the public price, in
blocks where space was actually scarce?

Everything runs from `run_pipeline.py`. Step 01 reads the public dataset once
(~300 GB); every later step works on local tables and costs cents.

```
01_tx_base       one pass over crypto_bitcoin.transactions
02_blocks        block -> mining pool, plus an empty floor_fee_rate column
03_txs           in-block CPFP edges, and the four non-relayable flags
04a_in_package   the subset that union-find has to look at
04b union-find   Python: packages priced as sum(fee) / sum(vbytes)
04c_update       package rates written back onto txs
05_block_floor   p05 of the effective rates in each block
05b_update       floor = median of the p05 of b-3..b-1, b+1..b+3
06a_fullness     which blocks were full, and had full neighbours
06b_flag_low_fee Flag A at 0.3 / 0.5 / 0.7 of the floor
07_revenue       the two value bands per flagged transaction
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
floor is NULL and the block takes no part in the flagging.

**Non-relayable traffic is excluded, not counted.** A transaction that a
default node of its day would refuse to relay never entered the public auction
at all, so its low price is explained by policy, not by a private deal. Four
tests, each against the rules in force on the day of the block:

| flag | rule |
|---|---|
| bare multisig | more than 3 pubkeys |
| OP_RETURN | scriptPubKey over 83 bytes before 2025-10-08 (Core v30), over 100,000 after |
| fee rate | under 1 sat/vB before 2025-09-04 (Core 29.1), under 0.1 after |
| size | virtual size over 100,000 vB (the 400k WU standard limit) |

The fee-rate test carries one carve-out: a sub-minimum parent with a paying
child in the same block is ordinary CPFP, not a private deal, and after Core 28
(2024-10-04) 1p1c package relay propagates it publicly. Only a sub-minimum
transaction with no paying in-block child is unambiguously non-relayable.
`flag_sub_minrelay_raw` keeps the uncarved version for comparison.

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

| | scanned | cost |
|---|---|---|
| step 01, full window | ~300 GB | ~$1.85 |
| every later step, full window | ~250 GB | ~$1.60 |
| one month, end to end (`--month`) | ~25 GB | ~$0.15 |

`tx_base` and `txs` are partitioned by month and clustered by block number;
`blocks` and the summary tables are small enough to need neither.

Every run prints what each step scanned. `--dry-run` prints it without running
anything, and the full run asks before the one expensive step unless `--yes`
is given.

## Running one month at a time

The source dataset is partitioned by month and the pipeline aggregates by
month, so the normal way to run this is one month per invocation:

```bash
python run_pipeline.py --month 2023-04
python delete_dataset.py
```

`--month` runs the full step list for that month, then exports it into
`out/` (merging into any earlier months already there — see
`export_results.py`). The BigQuery working dataset itself is left in place
so you can inspect it; once the local files hold what you need,
`delete_dataset.py` drops it so storage does not grow across months. Local
output stays on the order of megabytes per month.

## The accelerations dataset

`fetch_accelerations.py` and `run_accelerations.py` are a second, separate
pipeline: they crawl the mempool.space acceleration history, not
`crypto_bitcoin`, and write into their own dataset (`accelerations` by
default, `BQ_ACCEL_DATASET`) instead of the working dataset above. That
history is slow to (re)fetch and worth keeping, so `delete_dataset.py` — which
only ever touches `BQ_DATASET` — cannot take it out between months.

```bash
python fetch_accelerations.py          # top up: read only what is new
python fetch_accelerations.py --full   # re-crawl the whole history
python run_accelerations.py            # build the summary tables from it
```

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

The pace is deliberately slower than the API forces. It publishes no rate
limit, and at one request a second it pushed back constantly enough that the
backoff, not the sleep, set the real rate — about 5 pages a minute either way.
Asking slowly costs a top-up nothing, because a top-up reads a page or two
whatever the pace.

Three properties make the partial walk safe, and all three are tested in
`tests/test_fetch_accelerations.py`:

- **The watermark is a comparison, never a lookup.** Nothing has to still
  exist at that timestamp. If the record it came from were deleted upstream,
  `added > watermark` still selects exactly the records that follow it.
- **The list only grows at the top.** It is sorted by `added` descending and
  `added` never changes, so an insertion pushes records towards higher page
  numbers — never towards lower ones, where a downward walk has already been.
  A page number is therefore not a bookmark: half a page of new records moves
  every boundary by half a page.
- **The key is `(txid, added)`, not `txid`.** One transaction can carry more
  than one acceleration request — a retry after a failure — and both are real.
  Because `added` never changes, the same key also collapses a record the API
  returned twice.

The load is `WRITE_APPEND` of what is missing, so a short or interrupted
top-up can never shrink the table. Only `--full` replaces it.

Deletion upstream is the one shift the walk cannot see, and only a deletion
*during* a walk costs anything: a record removed above the page already read
pulls the one below it up into a page that has been passed. The exposure is
the length of the walk — about a minute for a top-up against about four hours
for `--full` — so the top-up is the safer run as well as the cheaper one, and
`--overlap` does not
help either way, since it extends where the walk stops rather than re-reading
where it has been.

A deletion *between* runs costs nothing. The watermark is a timestamp, so it
selects the same records whether or not the one it came from still exists;
the record simply stays in BigQuery after upstream drops it. Nothing is
skipped.

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

`validate_against_mempool.py` samples flagged blocks and compares them against
mempool.space block audits. `addedTxs` is a presence measure and Flag A is a
price measure, so partial overlap is the expected result. Transactions that
appear in `acceleratedTxs` were bought out of band through a public service:
they confirm the mechanism, and they are the part of the count that was never
invisible.
