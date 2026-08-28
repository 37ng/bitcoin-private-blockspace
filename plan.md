# Build the blockspace audit pipeline from human-context.md

## Context
- Fee rate is a **CPFP-group** property, not a per-tx one. A child paying
  for its parent means the pair transacted at one price, so grouping has
  to happen before any fee-rate statistic is computed.

- The floor is **neighbour-derived** — a block is judged against the
  blocks around it, never against itself. This is what makes the measure
  resistant to a pool stuffing its own block.

## Locked decisions

Settled by interrogation of the spec before planning:

| Branch | Decision |
|---|---|
| mempool.space role | Cross-check and `acceleratedTxs` anchor only — never a primary source |

**Sub-1 sat/vB alone flags ordinary CPFP parents.** A 0.2 sat/vB parent
with a paying child in the same block is routine, and after Core 28
(2024-10-04) 1p1c package relay propagates it publicly. Only a sub-1 tx
with no in-block paying child is unambiguously non-relayable.




## Verification

1. **Fixture tests** (`bigquery/tests/`). Synthetic blocks built as inline
   `UNNEST([STRUCT(...)])` and run through *the same SQL text* as the
   pipeline, with source tables substituted — one implementation, no
   parallel Python version to drift out of sync. Inline data scans 0
   bytes. Cases: a 3-deep CPFP chain collapsing to one group; a
   one-parent-three-children fan; a singleton; two disjoint chains in one
   block; a hand-computed p05; a 6-neighbour median computed by hand; a
   5-neighbour set that must return NULL; a sub-1 parent with a paying
   child either side of `PACKAGE_RELAY_DATE`. Tests skip when no
   application-default credentials are present.

2. **mempool.space cross-check** (`validate_against_mempool.py`). Samples
   ~50 low-fee-flagged blocks from the covered period (height ≳ 790,000), fetches
   `/api/v1/block/{hash}/audit-summary`, reports:
   - overlap between our flagged txs and their `addedTxs` — expect
     partial, since that set also holds latency and policy artifacts while
     ours is priced rather than presence-based. Report the rate honestly
     whatever it comes out at.
   - flagged txs appearing in `acceleratedTxs`. These were publicly
     accelerated, so they anchor `ACCEL_MARKUP` in step 08 — and each one
     is arguably a false positive for the off-chain claim. Record the
     count either way.

   Rate-limited with a sleep, responses cached to `bigquery/.cache/`,
   since each call returns a full block template.

3. **Pool share sanity check** (`sanity_check.py`, spec section "blocks").
   Prints each pool's share of blocks per month for comparison against a
   public hashrate chart. If a share is off by more than ~2 points,
   attribution is broken and the flagged-tx numbers mean nothing.

4. **End to end.** Full run, then read `low_fee_sensitivity` before
   believing any headline: if the number swings hard across the 3×3 grid,
   the finding is threshold-driven and has to be reported that way.

```bash
cd bigquery && python -m pytest tests/ -v && python run_pipeline.py && python validate_against_mempool.py && python sanity_check.py
```

Setup before the first run:

```bash
pip install -r bigquery/requirements.txt && gcloud auth application-default login
```