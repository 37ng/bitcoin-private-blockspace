"""Pull the full mempool.space acceleration history and load it to BigQuery.

An acceleration is an out-of-band payment. The sender pays mempool.space by
card or Lightning, mempool.space asks its partner pools to treat the
transaction as if it paid more, and the pool mines it. The transaction is
never modified, so the extra payment leaves no on-chain trace: the block
shows a transaction at a fee rate that could not have won its slot.

That makes this history the one labelled sample of the thing this project
measures. It is not the measurement itself -- it covers a single broker,
only from that broker's launch, and only standard transactions -- but every
record is a confirmed out-of-auction purchase with a known price.

Two payment fields exist and they are not interchangeable:

    feeDelta    sats credited to the miner for this transaction
    bidBoost    extra amount recorded against the v2 bidding model

The /accelerations/stats endpoint publishes `totalBidBoost` only, so it
cannot be trusted as "the total paid" without checking which field it
actually sums. This script totals both and prints the comparison, so the
answer rests on the records rather than on a field name.

    python fetch_accelerations.py              # top up: read only what is new
    python fetch_accelerations.py --full       # re-crawl the whole history
    python fetch_accelerations.py --no-load    # fetch and summarise only

A top-up is the normal run. The history endpoint is newest-first and takes no
`since` parameter, so a top-up walks from page 1 and stops once it has read
`--overlap` consecutive pages lying entirely at or before the watermark. The
watermark defaults to `MAX(added)` already in BigQuery.

The watermark is a comparison, never a lookup. Nothing has to still exist at
that timestamp for the walk to resume correctly -- `added > watermark` selects
the records that follow it, not the one it came from.

Ordering is what makes a partial walk safe. The list is sorted by `added`
descending and `added` never changes, so a new acceleration can only push
records towards higher page numbers -- never towards lower ones, where a
downward walk has already been. The worst a mid-walk insertion can do is show
one record twice, and the `(txid, added)` key absorbs that.

The list is append-only in practice, which is what lets a walk stop early at
all. Re-fetching a year of it nine days after the first load returned all
8,265 records with no field changed and none missing, and `x-total-count` has
only ever risen.

A page number is not a bookmark. Pages are a window onto that shifting list:
half a page of new records moves every boundary by half a page, so page 5
today is page 8 next week. Only `added` is stable enough to resume from.

Pages are cached under `${CACHE_DIR}/accelerations/` for `--full` only, so an
interrupted re-crawl resumes where it stopped. A top-up never reads the cache;
seeing what the cache does not have is its whole job.
"""

import argparse
import calendar
import json
import os
import random
import sys
import time

import requests

import config

API = "https://mempool.space/api/v1/services/accelerator/accelerations/history"
STATS_API = "https://mempool.space/api/v1/services/accelerator/accelerations/stats"

# The endpoint silently caps pageLength at 50; asking for more wastes nothing
# but returns no more rows.
MAX_PAGE_LENGTH = 50

# Seconds between requests: 3 a minute. The API publishes no rate limit, so
# the pace is a choice rather than a measurement, and at 1s it pushed back
# constantly -- the backoff, not the sleep, ended up setting the real rate.
# Asking slowly costs a top-up nothing: it reads a page or two either way.
DEFAULT_SLEEP = 20.0

HEADERS = {"User-Agent": "bitcoin-private-blockspace/1.0 (research)"}

SATS = 100_000_000


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


def fetch_stats(sleep):
    _, data = get_json(STATS_API, sleep=sleep)
    return data


def fetch_pages(sleep, page_length, max_pages, refresh):
    """Walk the paginated history, caching each page to disk.

    Pagination is newest-first, so records shift between pages while new
    accelerations arrive and the same record can be read twice. The key is
    `(txid, added)`, not txid: one transaction can carry more than one
    acceleration request, and dropping the second would lose a real record.
    A cached page is never refetched unless --refresh is given.
    """
    page_length = min(page_length, MAX_PAGE_LENGTH)
    directory = cache_dir()
    records = []
    seen = set()
    page = 1

    response, _ = get_json(API, {"page": 1, "pageLength": page_length}, sleep=sleep)
    total = int(response.headers.get("x-total-count", 0))
    expected_pages = (total + page_length - 1) // page_length if total else None
    if total:
        print(f"history reports {total} records "
              f"({expected_pages} pages of {page_length})")
        print(f"estimated wall time at {sleep}s per request: "
              f"{expected_pages * sleep / 60:.0f} min")

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

        for record in batch:
            key = (record["txid"], record.get("added"))
            if key not in seen:
                seen.add(key)
                records.append(record)

        if page % 20 == 0 or page == 1:
            # A pending acceleration has no block yet; ignore those when
            # reporting how far back the walk has reached.
            mined = [r["blockHeight"] for r in batch if r.get("blockHeight")]
            oldest = min(mined) if mined else "pending"
            print(f"    page {page:>4d}  {len(records):>6d} records  "
                  f"oldest height {oldest}")

        if len(batch) < page_length:
            print(f"page {page}: short page, history exhausted")
            break
        page += 1

    complete = not (max_pages and page > max_pages)
    print(f"fetched {len(records)} unique records over {page} pages")
    if complete and total and len(records) < total * 0.95:
        print(f"WARNING: expected about {total} records but hold {len(records)}. "
              f"The history may have moved during the run; rerun to fill gaps.")
    return records, complete


def utc(timestamp):
    """A unix timestamp as a readable UTC minute."""
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(timestamp))


def parse_since(text):
    """`--since` as a unix timestamp. Accepts a date or a full timestamp."""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return calendar.timegm(time.strptime(text, fmt))
        except ValueError:
            continue
    raise SystemExit(f"--since: cannot read {text!r} as a date")


def page_is_old(batch, watermark):
    """True when no record on this page is newer than the watermark.

    The stop test for a top-up. A record with no `added` counts as new, so an
    odd record is read again rather than skipped.
    """
    return all(r.get("added") is not None and r["added"] <= watermark
               for r in batch)


def accel_table():
    return f"{config.accel_dst()}.accelerations"


def watermark_from_bigquery():
    """Newest `added` already loaded, as a unix timestamp, or None.

    None means there is nothing to top up from -- no dataset, no table, or an
    empty one -- and the caller falls back to a full crawl.
    """
    import bqio
    from google.cloud.exceptions import NotFound
    try:
        bqio.client().get_table(accel_table())
    except NotFound:
        return None
    result = bqio.rows(
        f"SELECT UNIX_SECONDS(MAX(added)) AS newest FROM `{accel_table()}`")
    return result[0]["newest"] if result else None


def existing_keys():
    """The `(txid, added)` pairs already in BigQuery.

    A txid is not an identity. One transaction can carry more than one
    acceleration request -- a retry after a failure is the common case -- and
    both are real records. `added` separates them, and because it never
    changes it also collapses a record the API returned twice.
    """
    import bqio
    from google.cloud.exceptions import NotFound
    try:
        bqio.client().get_table(accel_table())
    except NotFound:
        return set()
    return {(r["txid"], int(r["added"].timestamp()) if r["added"] else None)
            for r in bqio.rows(f"SELECT txid, added FROM `{accel_table()}`")}


def unloaded(records, have):
    """The records not already in BigQuery, keyed on `(txid, added)`.

    The watermark decides only where to stop *reading*. What to *keep* is
    decided here, by key. Keeping the two apart is what makes an `added` equal
    to the watermark safe: such a record is read (its page counts as old) and
    then kept, because its key is not in `have`. A rule that filtered on
    `added > watermark` instead would lose it, and two accelerations can share
    a second.
    """
    return [r for r in records if (r["txid"], r.get("added")) not in have]


def fetch_new(sleep, page_length, watermark, overlap):
    """Walk from page 1 and stop once the walk is safely past the watermark.

    Every record read is returned, watermark or not; the loader drops what
    BigQuery already holds. Keeping them here makes the run's own summary
    describe what it actually read.
    """
    page_length = min(page_length, MAX_PAGE_LENGTH)
    records, seen = [], set()
    old_pages, page = 0, 1

    while True:
        _, batch = get_json(API, {"page": page, "pageLength": page_length},
                            sleep=sleep)
        if not batch:
            print(f"page {page}: empty, history exhausted")
            break

        for record in batch:
            key = (record["txid"], record.get("added"))
            if key not in seen:
                seen.add(key)
                records.append(record)

        if page_is_old(batch, watermark):
            old_pages += 1
            if old_pages >= overlap:
                print(f"page {page}: {overlap} consecutive pages at or before "
                      f"the watermark, stopping")
                break
        else:
            old_pages = 0

        if len(batch) < page_length:
            print(f"page {page}: short page, history exhausted")
            break
        page += 1

    fresh = sum(1 for r in records
                if r.get("added") is None or r["added"] > watermark)
    print(f"read {page} pages, {len(records)} records, {fresh} newer than the "
          f"watermark")
    return records


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


def load_to_bigquery(records, table="accelerations", append=False):
    """Write records to `${accel_dst}.accelerations`.

    A full crawl replaces the table -- about 30k rows, a few MB of storage. A
    top-up appends only what is not there yet, keyed on `(txid, added)`, so a
    short or interrupted fetch can never shrink what is already loaded.
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
    # Send timestamps as ISO strings rather than unix seconds, so the column
    # type is obvious from the payload instead of implied by the loader.
    for row in rows:
        for key in ("added", "last_updated"):
            if row[key]:
                row[key] = time.strftime("%Y-%m-%d %H:%M:%S",
                                         time.gmtime(row[key]))

    target = f"{config.accel_dst()}.{table}"
    disposition = "WRITE_APPEND" if append else "WRITE_TRUNCATE"
    job = bqio.client().load_table_from_json(
        rows, target,
        job_config=bq.LoadJobConfig(schema=schema(),
                                    write_disposition=disposition),
    )
    job.result()
    verb = "appended" if append else "loaded"
    print(f"{verb} {len(rows)} rows into {target}")


def monthly_table(paid):
    """Out-of-band spend per calendar month.

    Grouped on `added`, the time the acceleration was requested. A request is
    mined within minutes, so the month is the same either way, and `added` is
    present on every record while a block timestamp is not.
    """
    months = {}
    for record in paid:
        if not record.get("added"):
            continue
        month = time.strftime("%Y-%m", time.gmtime(record["added"]))
        bucket = months.setdefault(month, {"n": 0, "delta": 0, "vsize": 0,
                                           "onchain": 0})
        bucket["n"] += 1
        bucket["delta"] += record.get("feeDelta") or 0
        bucket["vsize"] += record.get("effectiveVsize") or 0
        bucket["onchain"] += record.get("effectiveFee") or 0

    print("\n=== out-of-band spend by month ===")
    print(f"{'month':<9}{'accels':>8}{'feeDelta sats':>16}{'BTC':>10}"
          f"{'vB':>12}{'off-chain':>11}{'on-chain':>10}")
    for month in sorted(months):
        b = months[month]
        off_rate = b["delta"] / b["vsize"] if b["vsize"] else 0
        on_rate = b["onchain"] / b["vsize"] if b["vsize"] else 0
        print(f"{month:<9}{b['n']:>8,d}{b['delta']:>16,d}"
              f"{b['delta'] / SATS:>10.4f}{b['vsize']:>12,d}"
              f"{off_rate:>10.1f}{on_rate:>10.1f}")
    return months


def summarise(records, stats, complete=True):
    """Answer the question the records can answer, in sats and BTC.

    Only mined, uncancelled accelerations count: a failed or cancelled request
    moved no money. `complete` is False for a truncated run, where totals are a
    sample of the newest records and carry no claim about the whole history.
    """
    paid = [r for r in records
            if not r.get("canceled")
            and (r.get("status") or "").startswith("completed")
            and r.get("blockHeight")]

    fee_delta = sum(r.get("feeDelta") or 0 for r in paid)
    bid_boost = sum(r.get("bidBoost") or 0 for r in paid)
    vsize = sum(r.get("effectiveVsize") or 0 for r in paid)
    onchain = sum(r.get("effectiveFee") or 0 for r in paid)

    header = "out-of-band fees collected"
    if not complete:
        header += " -- PARTIAL FETCH, newest records only"
    print(f"\n=== {header} ===")
    print(f"accelerations mined        {len(paid):>14,d} of {len(records):,d} records")
    print(f"sum(feeDelta)              {fee_delta:>14,d} sats   "
          f"{fee_delta / SATS:.4f} BTC")
    print(f"sum(bidBoost)              {bid_boost:>14,d} sats   "
          f"{bid_boost / SATS:.4f} BTC")
    print(f"sum(effectiveFee) on-chain {onchain:>14,d} sats   "
          f"{onchain / SATS:.4f} BTC")
    print(f"sum(effectiveVsize)        {vsize:>14,d} vB")
    if vsize:
        print(f"off-chain rate paid        {fee_delta / vsize:>14.1f} sat/vB "
              f"(on-chain {onchain / vsize:.1f})")

    if stats:
        print("\n--- against the published stats endpoint ---")
        print(f"totalRequested {stats.get('totalRequested'):>12,d}   "
              f"totalCompleted {stats.get('totalCompleted'):>12,d}")
        published = stats.get("totalBidBoost") or 0
        print(f"totalBidBoost  {published:>12,d} sats = {published / SATS:.4f} BTC")
        for name, value in (("feeDelta", fee_delta), ("bidBoost", bid_boost)):
            if published and abs(value - published) / published < 0.02:
                print(f"  -> published total matches sum({name}) within 2%")
        print(f"totalVsize     {stats.get('totalVsize'):>12,d} vB")

    if paid:
        heights = [r["blockHeight"] for r in paid]
        print(f"\nblock height range         {min(heights):,d} to {max(heights):,d}")
        first = min(r["added"] for r in paid if r.get("added"))
        print(f"earliest acceleration      "
              f"{time.strftime('%Y-%m-%d', time.gmtime(first))}")
        if complete:
            print("The history starts there because the service did not run "
                  "earlier, so it says nothing about the window before it.")
        else:
            print("This is the newest slice only -- not the start of the "
                  "history. Run --full for the real range.")
        monthly_table(paid)


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
                        help="re-crawl the whole history and replace the table")
    parser.add_argument("--since", metavar="YYYY-MM-DD",
                        help="top up from this time instead of the newest "
                             "record already in BigQuery")
    parser.add_argument("--overlap", type=int, default=2, metavar="N",
                        help="pages to read past the watermark (default 2)")
    args = parser.parse_args()

    stats = fetch_stats(args.sleep)

    watermark = None
    if not args.full:
        watermark = (parse_since(args.since) if args.since
                     else watermark_from_bigquery())
        if watermark is None:
            print("nothing loaded yet -- crawling the whole history")

    if watermark is None:
        records, complete = fetch_pages(args.sleep, args.page_length,
                                        args.max_pages, args.refresh)
        append = False
    else:
        print(f"topping up from {utc(watermark)} UTC")
        records = fetch_new(args.sleep, args.page_length, watermark,
                            args.overlap)
        complete, append = False, True

    if not records:
        print("no records fetched")
        return 1

    # `all.json` is the on-disk copy of the whole history. A top-up holds only
    # the newest slice, so writing it there would replace the history with a
    # fragment.
    if not append:
        with open(os.path.join(cache_dir(), "all.json"), "w") as fh:
            json.dump(records, fh)

    summarise(records, stats, complete)

    if not args.no_load:
        load_to_bigquery(records, append=append)
    return 0


if __name__ == "__main__":
    sys.exit(main())
