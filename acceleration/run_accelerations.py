"""Build the acceleration summary tables.

    python fetch_accelerations.py        # crawl mempool.space, load ${accel_dst}.accelerations
    python run_accelerations.py          # build the summary tables from it
    python run_accelerations.py --dry-run   # what each step would scan

Kept apart from `run_pipeline.py`: the accelerations history comes from the
mempool.space API, not `crypto_bitcoin`, and it lives in its own dataset
(`${accel_dst}`) so `delete_dataset.py` -- which drops the pipeline's `${dst}`
between months -- never takes it out with the disposable pipeline tables.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "utils"))

import bqio
import config

SQL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sql")

STEPS = [
    ("acceleration_monthly", "out-of-band spend per month"),
    ("acceleration_by_pool", "out-of-band spend per pool"),
]


def sql_name(step):
    return os.path.join(SQL_DIR, f"{step}.sql")


def dry_run(steps):
    total = 0
    for name, what in steps:
        try:
            scanned = bqio.dry_run(bqio.render(sql_name(name)))
        except Exception as exc:  # a step whose input table is not built yet
            first = str(exc).split("\n")[0]
            print(f"  {name:34s} needs an earlier step ({first[:60]})")
            continue
        total += scanned
        print(f"  {name:34s} {bqio.human_bytes(scanned):>10s}  "
              f"${bqio.usd(scanned):6.2f}  {what}")
    print(f"  {'total':34s} {bqio.human_bytes(total):>10s}  "
          f"${bqio.usd(total):6.2f}")
    return total


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="report bytes per step and stop")
    args = parser.parse_args()

    bqio.ensure_dataset(config.ACCEL_DATASET)

    if args.dry_run:
        dry_run(STEPS)
        return

    for name, what in STEPS:
        print(f"\n[{name}] {what}")
        bqio.run_file(sql_name(name), label=name)


if __name__ == "__main__":
    main()
