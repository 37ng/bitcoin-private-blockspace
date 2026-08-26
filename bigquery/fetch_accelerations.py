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

    python fetch_accelerations.py              # fetch, load, summarise
    python fetch_accelerations.py --no-load    # fetch and summarise only

Pages are cached under `${CACHE_DIR}/accelerations/`, so an interrupted run
resumes where it stopped and a rerun costs no requests.
"""

import argparse
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

HEADERS = {"User-Agent": "bitcoin-private-blockspace/1.0 (research)"}

SATS = 100_000_000


def cache_dir():
    path = os.path.join(config.CACHE_DIR, "accelerations")
    os.makedirs(path, exist_ok=True)
    return path


def get_json(url, params=None, sleep=1.0, attempts=8):
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
    accelerations arrive. Callers deduplicate by txid; a cached page is never
    refetched unless --refresh is given.
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

        new = [r for r in batch if r["txid"] not in seen]
        seen.update(r["txid"] for r in batch)
        records.extend(new)

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


def load_to_bigquery(records, table="accelerations"):
    """Replace `${dst}.accelerations`. About 30k rows, a few MB of storage."""
    from google.cloud import bigquery as bq

    import bqio

    bqio.ensure_dataset()
    rows = [normalise(r) for r in records]
    # Send timestamps as ISO strings rather than unix seconds, so the column
    # type is obvious from the payload instead of implied by the loader.
    for row in rows:
        for key in ("added", "last_updated"):
            if row[key]:
                row[key] = time.strftime("%Y-%m-%d %H:%M:%S",
                                         time.gmtime(row[key]))

    target = f"{config.dst()}.{table}"
    job = bqio.client().load_table_from_json(
        rows, target,
        job_config=bq.LoadJobConfig(schema=schema(),
                                    write_disposition="WRITE_TRUNCATE"),
    )
    job.result()
    print(f"loaded {len(rows)} rows into {target}")


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
                  "history. Run without --max-pages for the real range.")
        monthly_table(paid)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sleep", type=float, default=1.0,
                        help="seconds between requests (default 1.0)")
    parser.add_argument("--page-length", type=int, default=MAX_PAGE_LENGTH)
    parser.add_argument("--max-pages", type=int, default=0,
                        help="stop early, for a smoke test")
    parser.add_argument("--refresh", action="store_true",
                        help="ignore cached pages and refetch")
    parser.add_argument("--no-load", action="store_true",
                        help="skip the BigQuery load")
    args = parser.parse_args()

    stats = fetch_stats(args.sleep)
    records, complete = fetch_pages(args.sleep, args.page_length,
                                    args.max_pages, args.refresh)
    if not records:
        print("no records fetched")
        return 1

    with open(os.path.join(cache_dir(), "all.json"), "w") as fh:
        json.dump(records, fh)

    summarise(records, stats, complete)

    if not args.no_load:
        load_to_bigquery(records)
    return 0


if __name__ == "__main__":
    sys.exit(main())
