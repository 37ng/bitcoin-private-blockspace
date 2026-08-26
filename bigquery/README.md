# The pipeline

One question: how much block space changed hands below the public price, in
blocks where space was actually scarce?

Everything runs from `run_pipeline.py`. Step 01 reads the public dataset once
(~300 GB); every later step works on local tables and costs cents.

```
01_tx_base      one pass over crypto_bitcoin.transactions
02_blocks       block -> mining pool, plus an empty floor_fee_rate column
03_txs          in-block CPFP edges, and the four non-relayable flags
04_in_package   the subset that union-find has to look at
04b union-find  Python: packages priced as sum(fee) / sum(vbytes)
05_update       package rates written back onto txs
06_block_floor  p05 of the effective rates in each block
07_update       floor = median of the p05 of b-3..b-1, b+1..b+3
08_fullness     which blocks were full, and had full neighbours
09_flag_low_fee Flag A at 0.3 / 0.5 / 0.7 of the floor
10_revenue      the two value bands per flagged transaction
11_monthly      the monthly answer
12_pool         the same answer per pool
13_sensitivity  the 3x3 threshold grid
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
  `13_sensitivity` varies them.
- Pool attribution reads the coinbase tag first and the payout address second.
  Tags are matched case-insensitively; `refresh_pools.py` replaces the built-in
  table with the public mempool.space list.

## Cost

| | scanned | cost |
|---|---|---|
| step 01, full window | ~300 GB | ~$1.85 |
| every later step, full window | ~250 GB | ~$1.60 |
| one smoke month, end to end | ~25 GB | ~$0.15 |

`tx_base` and `txs` are partitioned by month and clustered by block number;
`blocks` and the summary tables are small enough to need neither. Storage for
the full window is roughly 60 GB.

Every run prints what each step scanned. `--dry-run` prints it without running
anything, and the full run asks before the one expensive step unless `--yes`
is given.

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
