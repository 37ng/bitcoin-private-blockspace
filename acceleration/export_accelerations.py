import argparse
import calendar
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "utils"))

import bqio
import config

FILENAME = "accelerations_monthly.json"
SATS = 100_000_000


# --- the completeness rule; pure, and tested in tests/ -------------------

def month_bounds(month):
    year, mon = int(month[:4]), int(month[5:7])
    start = calendar.timegm((year, mon, 1, 0, 0, 0, 0, 1, 0))
    end = calendar.timegm((year + mon // 12, mon % 12 + 1, 1, 0, 0, 0, 0, 1, 0))
    return start, end


def is_complete(month, oldest, newest):
    if oldest is None or newest is None:
        return False
    start, end = month_bounds(month)
    return oldest <= start and newest >= end


def hold_reason(month, oldest, newest):
    start, end = month_bounds(month)
    if oldest is not None and start < oldest:
        return "before the run"
    return "filling"


# --- reading what BigQuery holds ----------------------------------------

def run_bounds():
    result = bqio.rows(
        f"SELECT UNIX_SECONDS(MIN(added)) AS oldest, "
        f"UNIX_SECONDS(MAX(added)) AS newest "
        f"FROM `{config.accel_dst()}.accelerations`")
    if not result or result[0]["oldest"] is None:
        return None, None
    return result[0]["oldest"], result[0]["newest"]


def monthly_rows():
    return bqio.rows(
        f"SELECT FORMAT_DATE('%Y-%m', month) AS month, n_accelerations, "
        f"off_chain_sats, bid_boost_sats, on_chain_sats, vsize, "
        f"off_chain_sat_vb, on_chain_sat_vb "
        f"FROM `{config.accel_dst()}.acceleration_monthly` ORDER BY month")


# --- the file -----------------------------------------------------------

def build(rows, oldest, newest):
    months, held = [], []
    for r in rows:
        if not is_complete(r["month"], oldest, newest):
            held.append({"month": r["month"],
                         "n_accelerations": int(r["n_accelerations"]),
                         "reason": hold_reason(r["month"], oldest, newest)})
            continue
        months.append({
            "month": r["month"],
            "n_accelerations": int(r["n_accelerations"]),
            "off_chain_sats": int(r["off_chain_sats"] or 0),
            "off_chain_btc": round(int(r["off_chain_sats"] or 0) / SATS, 8),
            "bid_boost_sats": int(r["bid_boost_sats"] or 0),
            "on_chain_sats": int(r["on_chain_sats"] or 0),
            "vsize": int(r["vsize"] or 0),
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
        payload["total_accelerations"] = sum(m["n_accelerations"]
                                             for m in months)
        payload["total_off_chain_btc"] = round(
            sum(m["off_chain_sats"] for m in months) / SATS, 8)
    return payload, held


def write(payload, path):
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


def report(payload, held):
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

    filling = [h for h in held if h["reason"] == "filling"]
    below = [h for h in held if h["reason"] == "before the run"]

    for h in filling:
        print(f"\n{h['month']} is still filling ({h['n_accelerations']:,d} "
              f"records so far) and is held back until it ends. Nothing to do.")

    if below:
        print(f"\n=== {len(below)} months below the start of the run ===")
        print("The run does not reach back far enough to have read all of "
              "these, so they are excluded.")
        for h in below:
            print(f"  {h['month']}  {h['n_accelerations']:>6,d} records so far")
        first = below[0]["month"]
        print(f"\nTo take them in, extend the run past the start of {first}:")
        print(f"  uv run python fetch_accelerations.py --back-to {first}-01")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true",
                        help="report completeness and write nothing")
    parser.add_argument("--out",
                        default=os.path.join(config.DATA_DIR, FILENAME))
    args = parser.parse_args()

    oldest, newest = run_bounds()
    if oldest is None:
        print("the accelerations table is empty; nothing to publish")
        return 1
    print(f"the run spans {time.strftime('%Y-%m-%d', time.gmtime(oldest))} to "
          f"{time.strftime('%Y-%m-%d', time.gmtime(newest))}, with nothing "
          f"missing in between")

    payload, held = build(monthly_rows(), oldest, newest)
    report(payload, held)

    if args.check:
        return 0
    if not payload["months"]:
        print("\nnothing complete to publish yet; the file is left alone")
        return 1
    write(payload, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
