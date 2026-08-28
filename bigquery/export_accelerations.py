"""Publish the acceleration history as a small JSON file kept in git.

    python export_accelerations.py            # write data/accelerations_monthly.json
    python export_accelerations.py --check    # report completeness, write nothing
    python export_accelerations.py --seed-coverage   # once, for data loaded
                                                     # before the ledger existed

The write-up and any chart read this file, not BigQuery. It is small, it is
versioned, and a reader can reproduce a figure from it without credentials.

Only finished months go in. The unit of the analysis is a calendar month, so a
month is either whole or it is not evidence: a partial August tells you nothing
about August, and quoting it next to a full July invites a comparison that is
not there. Two separate things have to be true before a month is published.

    the month has ended        no more records can arrive in it
    the month has been read    some run actually walked the whole of it

The second is the one the data cannot answer by itself. A month half fetched
holds fewer records than a month fully fetched -- and so does a genuinely quiet
month. Nothing in the rows tells those apart, which is why
`fetch_accelerations.py` records what it read in `acceleration_coverage` and
this script merges those spans. A month is publishable when one merged span
holds the whole of it.

The first falls out of the second, so it needs no calendar rule of its own.
Coverage ends when the last run started, and that instant is inside the
current month, never past its end. The current month therefore fails the test
on its own arithmetic, and keeps failing until a run in the following month.
"""

import argparse
import calendar
import json
import os
import time

import bqio
import config

FILENAME = "accelerations_monthly.json"
SATS = 100_000_000


# --- coverage arithmetic; pure, and tested in tests/ ---------------------

def merge_spans(spans):
    """Overlapping or touching spans, merged into the fewest covering the same.

    The ledger is a pile, not a tidy list: every top-up re-reads `--overlap`
    pages, and ranges may be asked for in any order. Merging turns the pile
    into the only question the export asks of it -- which stretches of time
    have been read at all.

    Touching spans join. Two runs that met exactly at a second leave no gap
    between them, and a one second hole would otherwise fail a whole month.
    """
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


def month_bounds(month):
    """(start, end) of a "YYYY-MM" as unix seconds, end exclusive."""
    year, mon = int(month[:4]), int(month[5:7])
    start = calendar.timegm((year, mon, 1, 0, 0, 0, 0, 1, 0))
    end = calendar.timegm((year + mon // 12, mon % 12 + 1, 1, 0, 0, 0, 0, 1, 0))
    return start, end


def is_covered(month, merged):
    """True when a single merged span holds the whole month.

    One span, not several: two runs that between them touch every part of a
    month but leave a gap in the middle have not read that month. `merge_spans`
    has already joined everything that genuinely joins, so anything still
    separate is a real hole.
    """
    start, end = month_bounds(month)
    return any(s <= start and e >= end for s, e in merged)


def hold_reason(month, merged):
    """Why an unfinished month is not published: "filling" or "gap".

    The two need different answers from the reader. A month whose end lies past
    everything any run has read is simply not over yet -- the next top-up
    finishes it, and nothing needs doing. A month that ends inside the read
    range and is still not covered has a real hole, and only a backfill closes
    it.

    The test is against the newest recorded coverage rather than the clock, so
    it does not change between two runs of the same data.
    """
    _, end = month_bounds(month)
    newest = max(e for _, e in merged) if merged else 0
    return "filling" if end > newest else "gap"


def gap_before(month, merged):
    """The span of `month` that no run has read, or None.

    What to pass to `fetch_accelerations.py --since --until` to finish the
    month. It is the whole month unless a run reached partway into it.
    """
    start, end = month_bounds(month)
    for s, e in merged:
        if s <= start and e >= end:
            return None
        if s <= start < e < end:      # read from the start, stops inside
            return e, end
        if start < s <= end and e >= end:   # read to the end, starts inside
            return start, s
    return start, end


# --- reading what BigQuery holds ----------------------------------------

def coverage_spans():
    """The recorded spans, or None when the ledger does not exist yet."""
    from google.cloud.exceptions import NotFound
    table = f"{config.accel_dst()}.acceleration_coverage"
    try:
        bqio.client().get_table(table)
    except NotFound:
        return None
    return [(int(r["since"]), int(r["until"])) for r in bqio.rows(
        f"SELECT UNIX_SECONDS(since) AS since, UNIX_SECONDS(until) AS until "
        f"FROM `{table}`")]


def monthly_rows():
    return bqio.rows(
        f"SELECT FORMAT_DATE('%Y-%m', month) AS month, n_accelerations, "
        f"off_chain_sats, bid_boost_sats, on_chain_sats, vsize, "
        f"off_chain_sat_vb, on_chain_sat_vb "
        f"FROM `{config.accel_dst()}.acceleration_monthly` ORDER BY month")


def seed_coverage():
    """Claim the span the loaded records already span, once.

    For a table filled before the ledger existed. It is an assumption, not a
    measurement: it says the rows between the oldest and newest `added` are
    all there, which is true if they came from full crawls and top-ups, and
    false if a crawl was interrupted somewhere in the middle. Anything the
    seed gets wrong is fixed by re-fetching that range, which costs pages and
    no rows.
    """
    import fetch_accelerations as fa
    row = bqio.rows(f"SELECT UNIX_SECONDS(MIN(added)) AS lo, "
                    f"UNIX_SECONDS(MAX(added)) AS hi "
                    f"FROM `{config.accel_dst()}.accelerations`")
    lo, hi = (row[0]["lo"], row[0]["hi"]) if row else (None, None)
    if lo is None:
        raise SystemExit("the accelerations table is empty; nothing to seed")
    print(f"seeding coverage {fa.utc(lo)} to {fa.utc(hi)} UTC as 'assumed'.")
    print("This asserts those records are complete rather than checking it.")
    fa.record_coverage(lo, hi, "assumed", 0)


# --- the file -----------------------------------------------------------

def build(rows, merged):
    """(payload, skipped) -- the finished months, and why the rest are out."""
    months, skipped = [], []
    for r in rows:
        if not is_covered(r["month"], merged):
            gap = gap_before(r["month"], merged)
            entry = {"month": r["month"],
                     "n_accelerations": int(r["n_accelerations"]),
                     "reason": hold_reason(r["month"], merged)}
            if entry["reason"] == "gap":
                entry["fetch_since"] = time.strftime("%Y-%m-%d",
                                                     time.gmtime(gap[0]))
                entry["fetch_until"] = time.strftime("%Y-%m-%d",
                                                     time.gmtime(gap[1]))
            skipped.append(entry)
            continue
        vsize = int(r["vsize"] or 0)
        months.append({
            "month": r["month"],
            "n_accelerations": int(r["n_accelerations"]),
            "off_chain_sats": int(r["off_chain_sats"] or 0),
            "off_chain_btc": round(int(r["off_chain_sats"] or 0) / SATS, 8),
            "bid_boost_sats": int(r["bid_boost_sats"] or 0),
            "on_chain_sats": int(r["on_chain_sats"] or 0),
            "vsize": vsize,
            "off_chain_sat_vb": round(float(r["off_chain_sat_vb"]), 3)
            if r["off_chain_sat_vb"] is not None else None,
            "on_chain_sat_vb": round(float(r["on_chain_sat_vb"]), 3)
            if r["on_chain_sat_vb"] is not None else None,
        })

    payload = {
        "source": "mempool.space accelerator history",
        "unit": "one calendar month, complete months only",
        "note": ("An acceleration is an out-of-band payment: the sender pays "
                 "mempool.space, a partner pool mines the transaction as if it "
                 "had paid more, and nothing on chain records the difference. "
                 "off_chain_sats is what was paid beside the block; "
                 "on_chain_sats is what the same transactions paid inside it. "
                 "Only completed, mined accelerations count. This is one "
                 "broker, so it is a floor on out-of-band spend, not a total."),
        "months": months,
    }
    if months:
        payload["first_month"] = months[0]["month"]
        payload["last_month"] = months[-1]["month"]
        payload["total_off_chain_btc"] = round(
            sum(m["off_chain_sats"] for m in months) / SATS, 8)
        payload["total_accelerations"] = sum(m["n_accelerations"]
                                             for m in months)
    return payload, skipped


def write(payload, path):
    """Write only when the numbers changed.

    The file is in git, so rewriting an identical one every run would fill the
    history with commits that say nothing. There is no generated-at stamp in
    the payload for the same reason: it would change on every run and make
    every run look like new data.
    """
    text = json.dumps(payload, indent=2) + "\n"
    if os.path.exists(path):
        with open(path) as fh:
            if fh.read() == text:
                print(f"{path} is already up to date")
                return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)
    print(f"wrote {path}")
    return True


def report(payload, skipped, merged):
    months = payload["months"]
    print(f"\n=== {len(months)} complete months ===")
    if months:
        print(f"{'month':<9}{'accels':>8}{'off-chain BTC':>16}"
              f"{'off sat/vB':>12}{'on sat/vB':>11}")
        for m in months:
            print(f"{m['month']:<9}{m['n_accelerations']:>8,d}"
                  f"{m['off_chain_btc']:>16.4f}"
                  f"{m['off_chain_sat_vb'] or 0:>12.1f}"
                  f"{m['on_chain_sat_vb'] or 0:>11.1f}")
        print(f"{'total':<9}{payload['total_accelerations']:>8,d}"
              f"{payload['total_off_chain_btc']:>16.4f}")

    filling = [s for s in skipped if s["reason"] == "filling"]
    gaps = [s for s in skipped if s["reason"] == "gap"]

    for s in filling:
        print(f"\n{s['month']} is still filling ({s['n_accelerations']:,d} "
              f"records so far) and is held back until it ends. Nothing to do.")

    if not gaps:
        if not filling:
            print("\nEvery month with records has been read end to end.")
        return
    print(f"\n=== {len(gaps)} months with a gap ===")
    print("Each holds records already, but no run has read all of it, so the "
          "totals above exclude them.")
    for s in gaps:
        print(f"  {s['month']}  {s['n_accelerations']:>6,d} records so far  "
              f"needs {s['fetch_since']} to {s['fetch_until']}")
    first = gaps[0]
    print("\nThe oldest gap first:")
    print(f"  uv run python fetch_accelerations.py "
          f"--since {first['fetch_since']} --until {first['fetch_until']}")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true",
                        help="report completeness and write nothing")
    parser.add_argument("--seed-coverage", action="store_true",
                        help="claim the loaded records as read, once, for a "
                             "table filled before the ledger existed")
    parser.add_argument("--out", default=os.path.join(config.DATA_DIR, FILENAME))
    args = parser.parse_args()

    if args.seed_coverage:
        seed_coverage()
        return 0

    spans = coverage_spans()
    if spans is None:
        print("no coverage ledger yet, so no month can be shown to be "
              "complete.")
        print("Run --seed-coverage once if the table was filled before the "
              "ledger existed, or just fetch: any run records what it read.")
        return 1

    merged = merge_spans(spans)
    print(f"{len(spans)} recorded fetches, {len(merged)} continuous spans:")
    for s, e in merged:
        print(f"  {time.strftime('%Y-%m-%d', time.gmtime(s))} to "
              f"{time.strftime('%Y-%m-%d', time.gmtime(e))}")

    payload, skipped = build(monthly_rows(), merged)
    report(payload, skipped, merged)

    if args.check:
        return 0
    if not payload["months"]:
        print("\nnothing complete to publish yet; the file is left alone")
        return 1
    write(payload, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
