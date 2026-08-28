# The accelerations dataset

`fetch_accelerations.py` and `run_accelerations.py` are a second, separate
pipeline: they crawl the mempool.space acceleration history, not
`crypto_bitcoin`, and write into their own dataset (`accelerations` by
default, `BQ_ACCEL_DATASET`) instead of the [`raw/`](../raw/) pipeline's
working dataset. That history is slow to (re)fetch and worth keeping, so
`raw/delete_dataset.py` — which only ever touches `BQ_DATASET` — cannot take
it out between months.

`acceleration_by_pool.sql` joins against `${dst}.blocks` from `raw/`'s step
02 for pool attribution, so run the `raw/` pipeline for a month before
aggregating that month here. Both pipelines share `utils/config.py` and
`utils/bqio.py` — this directory adds them to `sys.path` at import time
rather than duplicating them.

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

## The invariant: one contiguous run

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

## Building the history

```bash
uv run python fetch_accelerations.py --full                # everything, replaces
uv run python fetch_accelerations.py --back-to 2024-01-01  # extend further back
```

`--full` crawls the whole list and replaces the table, so it is also the
wipe-and-reload — there is no separate delete step. At about 610 pages and one
request every 20 seconds it takes roughly four hours. Pages are cached under
`${CACHE_DIR}/accelerations/` (`.cache/accelerations/` by default, relative to
this directory), so an interrupted crawl resumes rather than starting over.

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

## What gets stored, and what counts

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

## What gets published

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

`run_accelerations.py`'s steps live in `sql/`, this directory's own step
list, separate from the numbered `01`-`08` pipeline chain in `raw/sql/`.

## Tests

```bash
uv run python -m pytest tests/ -q
```

Offline, no credentials, no cost — see `raw/README.md` for how the full test
suite (including the credentialed and BigQuery-dry-run tests) is organized.
