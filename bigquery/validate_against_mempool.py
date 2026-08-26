"""Cross-check flagged transactions against mempool.space block audits.

mempool.space keeps its own view of what a block should have contained. Its
`audit-summary` for a block reports, among other things:

    addedTxs        in the block but not in their expected template
    acceleratedTxs  paid for through their public acceleration service

Neither is the same measurement as Flag A. `addedTxs` also holds latency and
policy artifacts, while Flag A is about price, so the overlap is expected to be
partial. It is reported as it comes out, not tuned.

`acceleratedTxs` matters more: those transactions were paid for out of band
through a public service. A flagged transaction that appears there is a
confirmed out-of-auction purchase — and also a transaction whose extra payment
is publicly known, so it is arguably a false positive for the "off chain and
invisible" reading. The count is reported either way.

The audit API only covers recent history on the public instance; blocks below
roughly 790,000 usually return nothing. Responses are cached under
`${CACHE_DIR}` and the loop sleeps between calls, because each response is a
full block template.

    python validate_against_mempool.py --sample 50
"""

import argparse
import json
import os
import random
import time

import requests

import bqio
import config

API = "https://mempool.space/api/v1/block/{block_hash}/audit-summary"
MIN_AUDITED_HEIGHT = 790_000


def cache_path(block_hash):
    os.makedirs(config.CACHE_DIR, exist_ok=True)
    return os.path.join(config.CACHE_DIR, f"audit_{block_hash}.json")


def fetch_audit(block_hash, sleep, timeout=30):
    path = cache_path(block_hash)
    if os.path.exists(path):
        with open(path) as fh:
            return json.load(fh)
    response = requests.get(API.format(block_hash=block_hash),
                            timeout=timeout,
                            headers={"User-Agent": "private-blockspace-audit/1.0"})
    time.sleep(sleep)
    if response.status_code != 200:
        return None
    data = response.json()
    with open(path, "w") as fh:
        json.dump(data, fh)
    return data


def sample_blocks(sample, sensitivity_column):
    sql = bqio.render_string(f"""
      SELECT
        b.block_number,
        b.block_hash,
        b.pool_name,
        COUNT(*) AS flagged_txs,
        ARRAY_AGG(t.tx_hash LIMIT 2000) AS flagged_hashes
      FROM `${{dst}}.txs` AS t
      JOIN `${{dst}}.blocks` AS b USING (block_number)
      WHERE t.{sensitivity_column}
        AND b.block_number >= {MIN_AUDITED_HEIGHT}
      GROUP BY b.block_number, b.block_hash, b.pool_name
      ORDER BY b.block_number
    """)
    rows = bqio.rows(sql)
    if len(rows) > sample:
        random.seed(20260817)  # a fixed sample, so reruns are comparable
        rows = sorted(random.sample(rows, sample), key=lambda r: r["block_number"])
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sample", type=int, default=50)
    parser.add_argument("--sleep", type=float, default=1.5,
                        help="seconds between API calls")
    parser.add_argument("--sensitivity", choices=["30", "50", "70"], default="50")
    args = parser.parse_args()

    column = f"flag_a_{args.sensitivity}"
    blocks = sample_blocks(args.sample, column)
    if not blocks:
        print(f"no blocks with {column} above height {MIN_AUDITED_HEIGHT}")
        return

    print(f"checking {len(blocks)} blocks against mempool.space "
          f"(sensitivity 0.{args.sensitivity})\n")

    audited = 0
    total_flagged = 0
    total_in_added = 0
    total_in_accelerated = 0
    blocks_with_any_overlap = 0

    for block in blocks:
        data = fetch_audit(block["block_hash"], args.sleep)
        if not data:
            print(f"  {block['block_number']}  no audit data")
            continue
        audited += 1
        added = set(data.get("addedTxs") or [])
        accelerated = set(data.get("acceleratedTxs") or [])
        ours = set(block["flagged_hashes"])

        hit_added = len(ours & added)
        hit_accel = len(ours & accelerated)
        total_flagged += len(ours)
        total_in_added += hit_added
        total_in_accelerated += hit_accel
        if hit_added or hit_accel:
            blocks_with_any_overlap += 1

        print(f"  {block['block_number']}  {block['pool_name']:<16s} "
              f"flagged {len(ours):>4d}  in addedTxs {hit_added:>4d}  "
              f"accelerated {hit_accel:>3d}")

    if not audited:
        print("\nno block returned audit data; the public API keeps only "
              "recent history")
        return

    print(f"\nblocks with audit data      {audited} of {len(blocks)}")
    print(f"blocks with any overlap     {blocks_with_any_overlap}")
    print(f"flagged transactions        {total_flagged}")
    print(f"  also in addedTxs          {total_in_added} "
          f"({total_in_added / total_flagged * 100:.1f}%)")
    print(f"  also in acceleratedTxs    {total_in_accelerated} "
          f"({total_in_accelerated / total_flagged * 100:.1f}%)")
    print("\naddedTxs is a presence measure and Flag A is a price measure, so "
          "partial overlap is the expected result, not a failure.")
    print("Transactions in acceleratedTxs were bought out of band through a "
          "public service: they confirm the mechanism, and they are the part "
          "of the count that is not invisible.")


if __name__ == "__main__":
    main()
