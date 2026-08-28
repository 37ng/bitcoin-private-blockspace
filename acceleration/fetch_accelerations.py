"""Fetch the mempool.space acceleration history into BigQuery.

An acceleration is an out-of-band payment. The sender pays mempool.space by
card or Lightning, mempool.space asks its partner pools to treat the
transaction as if it paid more, and the pool mines it. The transaction is
never modified, so the extra payment leaves no on-chain trace: the block
shows a transaction at a fee rate that could not have won its slot.

That makes this history the one labelled sample of the thing this project
measures. It is not the measurement itself -- it covers a single broker,
only from that broker's launch, and only standard transactions -- but every
record is a confirmed out-of-auction purchase with a known price.

    python fetch_accelerations.py                       # top up with what is new
    python fetch_accelerations.py --full                # crawl everything, replace
    python fetch_accelerations.py --back-to 2024-01-01  # extend further back

This script only fetches. It forms no opinion about months, prints no totals
and writes no summary: `run_accelerations.py` aggregates and
`export_accelerations.py` decides what is complete enough to publish.

--- the one invariant -------------------------------------------------------

The table always holds ONE CONTIGUOUS RUN of the history, from some oldest
record up to the newest. Every mode here extends that run and none of them can
create an island, so between `MIN(added)` and `MAX(added)` nothing is missing.

That is the whole reason the export needs no bookkeeping. "Which stretch of
history do we hold?" is answered by two aggregates over the data itself, and
"is this month complete?" follows from them. A ledger of what had been fetched
would be a second source of truth, free to drift from the rows it describes.

Contiguity is not assumed, it is enforced. A top-up refuses to stop until it
has actually READ A RECORD IT ALREADY HAS -- touching the existing run is what
proves the new records join onto it rather than floating above a gap. A
backfill starts from the oldest record already held and walks down from there,
for the same reason.

This is also why there is no "fetch me an arbitrary date range". Such a range
would land a disconnected island; `MIN`/`MAX` would then span records that were
never read, and a half-fetched month would be published as a finished one. The
range machinery survives as `--back-to`, anchored to what is already held.

--- what gets stored --------------------------------------------------------

Only records in a terminal state: `completed`, `completed_provisional` and
`failed`. An `accelerating` record is still in flight and its status will
change, and because the key is `(txid, added)` and loads only ever append, a
record stored mid-flight would keep its stale status for good and never count
as revenue however it actually ended up. Skipping it costs nothing: a later
run reads it again, settled.

`failed` records are stored but earn nothing. They are kept so the question
"how many accelerations failed, and were their transactions mined anyway?"
stays askable. `export_accelerations.py` filters to `completed*` for every
revenue figure.

--- why a partial walk is safe ----------------------------------------------

Ordering. The list is sorted by `added` descending and `added` never changes,
so a new acceleration can only push records towards higher page numbers --
never towards lower ones, where a downward walk has already been. The worst a
mid-walk insertion can do is show one record twice, and the `(txid, added)` key
absorbs that. One transaction can also carry two real acceleration requests, a
retry after a failure, which is why the key is not the txid alone.

The list is append-only in practice, which is what lets a walk stop early at
all. Re-fetching a year of it nine days after the first load returned all
8,265 records with no field changed and none missing, and `x-total-count` has
only ever risen.

A page number is not a bookmark. Pages are a window onto that shifting list:
half a page of new records moves every boundary by half a page, so page 5
today is page 8 next week. Only `added` is stable enough to resume from.

Pages are cached under `${CACHE_DIR}/accelerations/` for `--full` only, so an
interrupted re-crawl resumes where it stopped. The other modes never read the
cache; seeing what the cache does not have is their whole job.
"""

import argparse
import calendar
import json
import os
import random
import sys
import time

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "raw"))

import config

API = "https://mempool.space/api/v1/services/accelerator/accelerations/history"

# The endpoint silently caps pageLength at 50; asking for more wastes nothing
# but returns no more rows.
MAX_PAGE_LENGTH = 50

# Seconds between requests: 3 a minute. The API publishes no rate limit, so
# the pace is a choice rather than a measurement, and at 1s it pushed back
# constantly -- the backoff, not the sleep, ended up setting the real rate.
# Asking slowly costs a top-up nothing: it reads a page or two either way.
DEFAULT_SLEEP = 20.0

HEADERS = {"User-Agent": "bitcoin-private-blockspace/1.0 (research)"}

# A record in one of these states will not change again, so it is safe to
# store under a key that is never updated. Anything else is still in flight.
TERMINAL = ("completed", "failed")


def cache_dir():
    path = os.path.join(config.CACHE_DIR, "accelerations")
    os.makedirs(path, exist_ok=True)
    return path


def get_json(url, params=None, sleep=DEFAULT_SLEEP, attempts=8):
    """One GET with polite backoff.

    The public API publishes no rate-limit headers, so the client sets its own
    pace and backs off on push-back rather than discovering the limit by being
    blocked. Push-back arrives in two forms and both must be retried: an HTTP
    429/5xx, and a dropped connection -- under load the server resets the
    socket instead of answering, which surfaces as a requests exception rather
    than a status code.
    """
    delay = sleep
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(url, params=params, headers=HEADERS,
                                    timeout=30)
        except requests.exceptions.RequestException as exc:
            delay = min(max(delay * 3, 5), 120)
            print(f"    {type(exc).__name__}, backing off {delay:.0f}s "
                  f"(attempt {attempt}/{attempts})")
            time.sleep(delay)
            continue
        if response.status_code == 200:
            # Jitter keeps a long run from settling into a fixed cadence that
            # looks like a scripted hammer.
            time.sleep(sleep * random.uniform(0.8, 1.4))
            return response, response.json()
        if response.status_code in (429, 500, 502, 503, 504):
            delay = min(max(delay * 3, 5), 120)
            print(f"    HTTP {response.status_code}, backing off {delay:.0f}s "
                  f"(attempt {attempt}/{attempts})")
            time.sleep(delay)
            continue
        response.raise_for_status()
    raise RuntimeError(f"{url}: gave up after {attempts} attempts")


# --- small pure helpers --------------------------------------------------

def utc(timestamp):
    """A unix timestamp as a readable UTC minute."""
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(timestamp))


def iso(timestamp):
    """A unix timestamp as the string BigQuery reads as a TIMESTAMP.

    Sent instead of unix seconds so the column type is obvious from the
    payload rather than implied by the loader.
    """
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(timestamp))


def parse_time(text):
    """A date bound as a unix timestamp.

    Accepts a date or a full timestamp, and reads both as UTC -- the same clock
    the API's `added` field uses, so a bound means the same thing wherever the
    script is run.
    """
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return calendar.timegm(time.strptime(text, fmt))
        except ValueError:
            continue
    raise SystemExit(f"cannot read {text!r} as a date")


def key_of(record):
    """The identity of an acceleration request.

    Not the txid. One transaction can carry more than one request -- a retry
    after a failure is the common case -- and both are real records. `added`
    separates them, and because it never changes it also collapses a record
    the API returned twice.
    """
    return record["txid"], record.get("added")


def is_settled(record):
    """True when this record's status will not change again.

    Storing an in-flight record would freeze it. Loads only append, keyed on
    `(txid, added)`, so the `accelerating` version already in the table is the
    version that stays there, and it would never count as revenue however it
    actually ended up. A later run reads it again once it has settled.
    """
    return (record.get("status") or "").startswith(TERMINAL)


def page_is_old(batch, bound):
    """True when no record on this page is newer than `bound`.

    A record with no `added` counts as new, so an odd record is read again
    rather than skipped.
    """
    return all(r.get("added") is not None and r["added"] <= bound
               for r in batch)


def collect(batch, records, seen, kept_keys=None):
    """Keep the settled, unseen records from one page. Returns how many.

    `kept_keys` collects the keys of everything read, settled or not, which is
    what the top-up uses to notice it has reached records it already holds.
    """
    kept = 0
    for record in batch:
        key = key_of(record)
        if kept_keys is not None:
            kept_keys.add(key)
        if not is_settled(record) or key in seen:
            continue
        seen.add(key)
        records.append(record)
        kept += 1
    return kept


# --- walking the list ----------------------------------------------------

def history_size(page_length, sleep):
    """(records, pages) as the endpoint reports them right now.

    `x-total-count` comes back with any page, so this costs one request and
    gives a walk something to size itself against. Both numbers are a snapshot:
    the list grows at the top while a long walk is running.
    """
    response, _ = get_json(API, {"page": 1, "pageLength": page_length},
                           sleep=sleep)
    total = int(response.headers.get("x-total-count", 0))
    pages = (total + page_length - 1) // page_length if total else 0
    if total:
        print(f"history reports {total} records "
              f"({pages} pages of {page_length})")
    return total, pages


def seek_page(bound, page_length, pages, sleep):
    """The first page whose newest record is at or before `bound`.

    Walking to a page far down the list costs one request per page passed. The
    list is sorted by `added` descending, so the page holding a given time can
    be found by halving the page range instead: about 10 requests for 600
    pages, whatever the sleep.

    Drift during the search is safe in the same way the downward walk is. New
    accelerations only push records towards higher page numbers, so a page
    measured a minute ago can only hold newer records now -- the walk then
    starts a little too high, which costs a page, never a record. Anything the
    search cannot compare (an empty page, a record with no `added`) is treated
    as at-or-before, which sends the search towards lower page numbers and errs
    the same safe way.
    """
    lo, hi = 1, max(pages, 1)
    while lo < hi:
        mid = (lo + hi) // 2
        _, batch = get_json(API, {"page": mid, "pageLength": page_length},
                            sleep=sleep)
        newest = batch[0].get("added") if batch else None
        print(f"    seek page {mid:>4d}  newest "
              f"{utc(newest) if newest else '-'}")
        if newest is not None and newest > bound:
            lo = mid + 1
        else:
            hi = mid
    return lo


def fetch_full(sleep, page_length, max_pages, refresh):
    """Crawl the whole history from page 1 to the end.

    This is the only mode that builds the run from nothing, so it is also the
    wipe-and-reload: the caller replaces the table with what comes back. A
    crawl cut short still returns a contiguous run from the newest record
    downwards, just a shorter one, so replacing the table with it stays safe.

    A cached page is never refetched unless --refresh is given, which is what
    lets an interrupted crawl resume instead of starting over.
    """
    page_length = min(page_length, MAX_PAGE_LENGTH)
    directory = cache_dir()
    records, seen = [], set()
    page, read = 1, 0

    total, pages = history_size(page_length, sleep)
    if total:
        print(f"estimated wall time at {sleep}s per request: "
              f"{pages * sleep / 60:.0f} min")

    while True:
        if max_pages and page > max_pages:
            print(f"stopping at --max-pages {max_pages}")
            break

        path = os.path.join(directory, f"page_{page:05d}.json")
        if os.path.exists(path) and not refresh:
            with open(path) as fh:
                batch = json.load(fh)
        else:
            _, batch = get_json(API, {"page": page, "pageLength": page_length},
                                sleep=sleep)
            with open(path, "w") as fh:
                json.dump(batch, fh)

        if not batch:
            print(f"page {page}: empty, history exhausted")
            break
        read += len(batch)
        collect(batch, records, seen)

        if page % 20 == 0 or page == 1:
            oldest = min((r["added"] for r in batch if r.get("added")),
                         default=None)
            print(f"    page {page:>4d}  {len(records):>6d} settled  "
                  f"oldest {utc(oldest) if oldest else 'pending'}")

        if len(batch) < page_length:
            print(f"page {page}: short page, history exhausted")
            break
        page += 1

    print(f"read {page} pages, {read} records, kept {len(records)} settled")
    return records


def fetch_top_up(sleep, page_length, have, overlap):
    """Walk from page 1 until the walk touches records already held.

    The stop rule is the invariant. Reading down until a record we already have
    turns up is what proves the new records sit directly on top of the existing
    run with nothing missing in between; stopping on a timestamp alone would
    only prove we had gone far enough back in time, which is not the same
    thing. `overlap` more pages are read after the first touch, so a record
    that shifted pages mid-walk is still seen.

    Everything read is returned. The loader drops what is already there.
    """
    page_length = min(page_length, MAX_PAGE_LENGTH)
    records, seen = [], set()
    page, read = 1, 0
    touched, pages_since_touch = False, 0

    while True:
        _, batch = get_json(API, {"page": page, "pageLength": page_length},
                            sleep=sleep)
        if not batch:
            print(f"page {page}: empty, history exhausted")
            break
        read += len(batch)

        keys = set()
        collect(batch, records, seen, keys)
        if keys & have:
            if not touched:
                print(f"page {page}: reached records already held")
            touched = True

        if touched:
            pages_since_touch += 1
            if pages_since_touch > overlap:
                break
        if len(batch) < page_length:
            print(f"page {page}: short page, history exhausted")
            break
        page += 1

    if not touched:
        raise SystemExit(
            f"read {page} pages without reaching any record already held.\n"
            f"Stopping rather than loading them: they would sit above a gap "
            f"and the run would no longer be contiguous.\n"
            f"Run --full to rebuild, or raise --overlap if this was a very "
            f"long absence.")
    print(f"read {page} pages, {read} records, kept {len(records)} settled")
    return records


def fetch_back_to(sleep, page_length, oldest_held, target, overlap, max_pages):
    """Extend the run downwards from `oldest_held` to `target`.

    Anchored to the oldest record already held, not to a free-floating date,
    so what comes back joins the bottom of the run instead of forming an
    island. The seek jumps to that anchor -- about 10 requests instead of the
    hundreds of pages it would take to walk there -- and then the walk goes
    down from `overlap` pages above it, which is what makes the join overlap
    rather than merely meet.
    """
    page_length = min(page_length, MAX_PAGE_LENGTH)
    _, pages = history_size(page_length, sleep)

    page = 1
    if pages:
        page = max(1, seek_page(oldest_held, page_length, pages, sleep)
                   - overlap)
        remaining = pages - page + 1
        print(f"entering at page {page} of {pages}: at most {remaining} pages "
              f"to walk, {remaining * sleep / 60:.0f} min at {sleep}s each")

    records, seen = [], set()
    read, old_pages, walked = 0, 0, 0

    while True:
        if max_pages and walked >= max_pages:
            print(f"stopping at --max-pages {max_pages}")
            break

        _, batch = get_json(API, {"page": page, "pageLength": page_length},
                            sleep=sleep)
        walked += 1
        if not batch:
            print(f"page {page}: empty, history exhausted")
            break
        read += len(batch)
        collect(batch, records, seen)

        if walked == 1 or walked % 20 == 0:
            oldest = min((r["added"] for r in batch if r.get("added")),
                         default=None)
            print(f"    page {page:>4d}  {len(records):>6d} settled  "
                  f"oldest {utc(oldest) if oldest else 'pending'}")

        if page_is_old(batch, target):
            old_pages += 1
            if old_pages >= overlap:
                print(f"page {page}: {overlap} consecutive pages at or before "
                      f"{utc(target)}, stopping")
                break
        else:
            old_pages = 0

        if len(batch) < page_length:
            print(f"page {page}: short page, history exhausted")
            break
        page += 1

    print(f"walked {walked} pages, read {read} records, "
          f"kept {len(records)} settled")
    return records


# --- BigQuery ------------------------------------------------------------

def accel_table():
    return f"{config.accel_dst()}.accelerations"


def table_missing():
    from google.cloud.exceptions import NotFound
    import bqio
    try:
        bqio.client().get_table(accel_table())
        return False
    except NotFound:
        return True


def run_bounds():
    """(oldest, newest) `added` already loaded, as unix seconds, or (None, None).

    The two ends of the contiguous run. Everything between them has been read,
    which is the fact the whole design rests on.
    """
    import bqio
    if table_missing():
        return None, None
    result = bqio.rows(
        f"SELECT UNIX_SECONDS(MIN(added)) AS oldest, "
        f"UNIX_SECONDS(MAX(added)) AS newest FROM `{accel_table()}`")
    if not result or result[0]["oldest"] is None:
        return None, None
    return result[0]["oldest"], result[0]["newest"]


def existing_keys():
    """Every `(txid, added)` pair already in BigQuery.

    Used twice: to notice when a top-up has touched the existing run, and to
    decide what is genuinely new to append.
    """
    import bqio
    if table_missing():
        return set()
    return {(r["txid"], int(r["added"].timestamp()) if r["added"] else None)
            for r in bqio.rows(f"SELECT txid, added FROM `{accel_table()}`")}


def unloaded(records, have):
    """The records not already in BigQuery, keyed on `(txid, added)`."""
    return [r for r in records if key_of(r) not in have]


def normalise(record):
    """API record -> one BigQuery row.

    `canceled` arrives as 0/1 and `added`/`lastUpdated` as unix seconds.
    Everything else is passed through under a snake_case name.
    """
    return {
        "txid": record["txid"],
        "status": record.get("status"),
        "canceled": bool(record.get("canceled")),
        "added": record.get("added"),
        "last_updated": record.get("lastUpdated"),
        "effective_fee": record.get("effectiveFee"),
        "effective_vsize": record.get("effectiveVsize"),
        "fee_delta": record.get("feeDelta"),
        "bid_boost": record.get("bidBoost"),
        "boost_version": record.get("boostVersion"),
        "block_hash": record.get("blockHash"),
        "block_height": record.get("blockHeight"),
        "mined_by_pool_unique_id": record.get("minedByPoolUniqueId"),
        "pools": record.get("pools") or [],
    }


def schema():
    from google.cloud import bigquery as bq
    field = bq.SchemaField
    return [
        field("txid", "STRING", mode="REQUIRED"),
        field("status", "STRING"),
        field("canceled", "BOOL"),
        field("added", "TIMESTAMP"),
        field("last_updated", "TIMESTAMP"),
        field("effective_fee", "INT64"),
        field("effective_vsize", "INT64"),
        field("fee_delta", "INT64"),
        field("bid_boost", "INT64"),
        field("boost_version", "STRING"),
        field("block_hash", "STRING"),
        field("block_height", "INT64"),
        field("mined_by_pool_unique_id", "INT64"),
        field("pools", "INT64", mode="REPEATED"),
    ]


def load_to_bigquery(records, append):
    """Write records to `${accel_dst}.accelerations`.

    A `--full` crawl replaces the table -- about 30k rows, a few MB of storage
    -- because what it returns is the whole run. Every other mode extends the
    run and therefore appends only what is missing, keyed on `(txid, added)`,
    so a short or interrupted fetch can never shrink what is already loaded.
    """
    from google.cloud import bigquery as bq

    import bqio

    bqio.ensure_dataset(config.ACCEL_DATASET)
    if append:
        seen = len(records)
        records = unloaded(records, existing_keys())
        print(f"{seen - len(records)} of {seen} records were already loaded")
        if not records:
            print("nothing to append")
            return
    rows = [normalise(r) for r in records]
    for row in rows:
        for field in ("added", "last_updated"):
            if row[field]:
                row[field] = iso(row[field])

    disposition = "WRITE_APPEND" if append else "WRITE_TRUNCATE"
    job = bqio.client().load_table_from_json(
        rows, accel_table(),
        job_config=bq.LoadJobConfig(schema=schema(),
                                    write_disposition=disposition),
    )
    job.result()
    verb = "appended" if append else "loaded"
    print(f"{verb} {len(rows)} rows into {accel_table()}")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sleep", type=float, default=DEFAULT_SLEEP,
                        help=f"seconds between requests "
                             f"(default {DEFAULT_SLEEP:.0f}, about 3 a minute)")
    parser.add_argument("--page-length", type=int, default=MAX_PAGE_LENGTH)
    parser.add_argument("--max-pages", type=int, default=0,
                        help="stop early, for a smoke test")
    parser.add_argument("--refresh", action="store_true",
                        help="ignore cached pages and refetch")
    parser.add_argument("--no-load", action="store_true",
                        help="skip the BigQuery load")
    parser.add_argument("--full", action="store_true",
                        help="crawl the whole history and replace the table")
    parser.add_argument("--back-to", metavar="YYYY-MM-DD",
                        help="extend the run further back, down to this date")
    parser.add_argument("--overlap", type=int, default=2, metavar="N",
                        help="pages to read past the join (default 2)")
    parser.add_argument("--yes", action="store_true",
                        help="do not ask before replacing the table")
    args = parser.parse_args()

    if args.full and args.back_to:
        raise SystemExit("--full already reads everything; drop --back-to")

    target = parse_time(args.back_to) if args.back_to else None
    oldest, newest = (None, None) if args.full else run_bounds()

    if oldest is None and not args.full:
        print("nothing loaded yet -- crawling the whole history")
        args.full = True

    if args.full:
        if args.max_pages and not args.yes and not args.no_load:
            print(f"--full replaces the table, and --max-pages {args.max_pages} "
                  f"means it would be replaced with a fragment.")
            import bqio
            if not bqio.confirm("Continue?"):
                return 1
        records = fetch_full(args.sleep, args.page_length, args.max_pages,
                             args.refresh)
        append = False
    elif target is not None:
        if target >= oldest:
            print(f"already hold records back to {utc(oldest)} UTC; "
                  f"--back-to {args.back_to} asks for nothing new")
            return 0
        print(f"extending the run from {utc(oldest)} back to {utc(target)} UTC")
        records = fetch_back_to(args.sleep, args.page_length, oldest, target,
                                args.overlap, args.max_pages)
        append = True
    else:
        print(f"topping up; the run currently ends at {utc(newest)} UTC")
        records = fetch_top_up(args.sleep, args.page_length, existing_keys(),
                               args.overlap)
        append = True

    if not records:
        print("no settled records fetched")
        return 0 if append else 1

    if not args.no_load:
        load_to_bigquery(records, append=append)
        lo, hi = run_bounds()
        if lo is not None:
            print(f"the run now spans {utc(lo)} to {utc(hi)} UTC")
    return 0


if __name__ == "__main__":
    sys.exit(main())
