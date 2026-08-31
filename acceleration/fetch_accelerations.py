import argparse
import calendar
import os
import random
import sys
import time

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "utils"))

import config

API = "https://mempool.space/api/v1/services/accelerator/accelerations/history"

# The endpoint caps pageLength at 50.
MAX_PAGE_LENGTH = 50

# Seconds between requests. The API publishes no rate limit, so the pace is a
# choice: at 1s it pushed back constantly and the backoff, not the sleep, set
# the real rate.
DEFAULT_SLEEP = 20.0

# Days of `added` time per request window. A window is walked page by page, so
# a wide one means deep page numbers; a narrow one means more empty requests.
DEFAULT_WINDOW_DAYS = 30

# A default run restarts this far behind the newest record already held. A
# record that was still in flight during the last run was skipped, and this is
# the second chance to read it once it settled.
LOOKBACK_DAYS = 3

HEADERS = {"User-Agent": "bitcoin-private-blockspace/1.0 (research)"}

# A record in one of these states will not change again, so it is safe to
# store under a key that is never updated. Anything else is still in flight.
TERMINAL = ("completed", "failed")


def get_json(params, sleep=DEFAULT_SLEEP, attempts=8):
    delay = sleep
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(API, params=params, headers=HEADERS,
                                    timeout=30)
        except requests.exceptions.RequestException as exc:
            delay = min(max(delay * 3, 5), 120)
            print(f"    {type(exc).__name__}, backing off {delay:.0f}s "
                  f"(attempt {attempt}/{attempts})")
            time.sleep(delay)
            continue
        if response.status_code == 200:
            # Jitter keeps a long run off a fixed cadence.
            time.sleep(sleep * random.uniform(0.8, 1.4))
            return response, response.json()
        if response.status_code in (429, 500, 502, 503, 504):
            delay = min(max(delay * 3, 5), 120)
            print(f"    HTTP {response.status_code}, backing off {delay:.0f}s "
                  f"(attempt {attempt}/{attempts})")
            time.sleep(delay)
            continue
        response.raise_for_status()
    raise RuntimeError(f"{API}: gave up after {attempts} attempts")


# --- small pure helpers --------------------------------------------------

def utc(timestamp):
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(timestamp))


def iso(timestamp):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(timestamp))


def parse_time(text):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return calendar.timegm(time.strptime(text, fmt))
        except ValueError:
            continue
    raise SystemExit(f"cannot read {text!r} as a date")


def key_of(record):
    return record["txid"], record.get("added")


def is_settled(record):
    return (record.get("status") or "").startswith(TERMINAL)


def collect(batch, records, seen):
    kept = 0
    for record in batch:
        key = key_of(record)
        if not is_settled(record) or key in seen:
            continue
        seen.add(key)
        records.append(record)
        kept += 1
    return kept


def windows(start, end, days):
    # `from` and `to` are both inclusive, so a window ends one second before
    # the next one starts and no record is read twice.
    span = days * 86400
    while start <= end:
        stop = min(start + span - 1, end)
        yield start, stop
        start = stop + 1


# --- reading one window --------------------------------------------------

def fetch_window(start, end, sleep, page_length):
    records, seen = [], set()
    page, read, total = 1, 0, None

    while True:
        response, batch = get_json(
            {"from": start, "to": end, "page": page,
             "pageLength": page_length}, sleep=sleep)
        if total is None and response is not None:
            total = int(response.headers.get("x-total-count", 0)) or None
        if not batch:
            break
        read += len(batch)
        collect(batch, records, seen)
        if len(batch) < page_length:
            break
        if total and read >= total:
            break
        page += 1

    return records, read


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
    import bqio
    if table_missing():
        return set()
    return {(r["txid"], int(r["added"].timestamp()) if r["added"] else None)
            for r in bqio.rows(f"SELECT txid, added FROM `{accel_table()}`")}


def drop_table():
    import bqio
    bqio.client().delete_table(accel_table(), not_found_ok=True)
    print(f"dropped {accel_table()}")


def normalise(record):
    return {
        "txid": record["txid"],
        "status": record.get("status"),
        "canceled": bool(record.get("canceled")),
        "added": iso(record["added"]) if record.get("added") else None,
        "last_updated": iso(record["lastUpdated"])
        if record.get("lastUpdated") else None,
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


def append(records, have):
    from google.cloud import bigquery as bq

    import bqio
    fresh = [r for r in records if key_of(r) not in have]
    if not fresh:
        return 0
    job = bqio.client().load_table_from_json(
        [normalise(r) for r in fresh], accel_table(),
        job_config=bq.LoadJobConfig(schema=schema(),
                                    write_disposition="WRITE_APPEND"),
    )
    job.result()
    have.update(key_of(r) for r in fresh)
    return len(fresh)


# --- the run -------------------------------------------------------------

def start_of_run(args, replace):
    if args.start:
        return parse_time(args.start)
    _, newest = (None, None) if replace else run_bounds()
    if newest is None:
        print(f"nothing loaded yet -- reading from {config.START_DATE}")
        return parse_time(config.START_DATE)
    start = newest - LOOKBACK_DAYS * 86400
    print(f"the run ends at {utc(newest)} UTC; re-reading from {utc(start)} "
          f"in case anything settled since")
    return start


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from", dest="start", metavar="YYYY-MM-DD",
                        help="read from this time (default: just behind the "
                             "newest record already held)")
    parser.add_argument("--to", dest="end", metavar="YYYY-MM-DD",
                        help="read up to this time (default: now)")
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW_DAYS,
                        metavar="DAYS",
                        help=f"days per request window "
                             f"(default {DEFAULT_WINDOW_DAYS})")
    parser.add_argument("--sleep", type=float, default=DEFAULT_SLEEP,
                        help=f"seconds between requests "
                             f"(default {DEFAULT_SLEEP:.0f})")
    parser.add_argument("--page-length", type=int, default=MAX_PAGE_LENGTH)
    parser.add_argument("--no-load", action="store_true",
                        help="fetch and report, but write nothing")
    parser.add_argument("--replace", action="store_true",
                        help="drop the table first and rebuild it")
    parser.add_argument("--yes", action="store_true",
                        help="do not ask before dropping the table")
    args = parser.parse_args()

    page_length = min(args.page_length, MAX_PAGE_LENGTH)
    end = parse_time(args.end) if args.end else int(time.time())
    start = start_of_run(args, args.replace)
    if start > end:
        raise SystemExit(f"{utc(start)} is after {utc(end)}; nothing to read")

    plan = list(windows(start, end, args.window))
    print(f"reading {utc(start)} to {utc(end)} UTC in {len(plan)} windows of "
          f"{args.window} days, at least {len(plan) * args.sleep / 60:.0f} min "
          f"at {args.sleep:.0f}s a request")

    have = set()
    if not args.no_load:
        import bqio
        if args.replace:
            if not args.yes and not bqio.confirm(
                    f"drop and rebuild {accel_table()}?"):
                return 1
            drop_table()
        bqio.ensure_dataset(config.ACCEL_DATASET)
        have = existing_keys()

    read = kept = loaded = 0
    for window_start, window_end in plan:
        records, in_window = fetch_window(window_start, window_end, args.sleep,
                                          page_length)
        read += in_window
        kept += len(records)
        new = 0
        if records and not args.no_load:
            new = append(records, have)
            loaded += new
        print(f"    {utc(window_start)}..{utc(window_end)}  "
              f"{in_window:>5d} read  {len(records):>5d} settled  "
              f"{new:>5d} new")

    print(f"read {read} records, {kept} settled, loaded {loaded}")
    if not args.no_load:
        oldest, newest = run_bounds()
        if oldest is not None:
            print(f"the run now spans {utc(oldest)} to {utc(newest)} UTC")
    return 0


if __name__ == "__main__":
    sys.exit(main())
