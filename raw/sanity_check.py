"""Is the pool attribution real?

Prints each pool's share of blocks per month. Compare against a public
hashrate chart (mempool.space/graphs/mining/pools, or blockchain.com). A share
off by more than about 2 points means the coinbase tag table in `pools.py` has
gone stale, and every per-pool number downstream is then worthless.

The share of blocks left unattributed is the number to watch first: a few
percent is normal for solo and small miners, but a jump means a large pool
changed its coinbase tag.

    python sanity_check.py               # last 12 months
    python sanity_check.py --all         # the whole window
    python sanity_check.py --top 15
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "utils"))

import bqio


def monthly_shares(months, top):
    sql = bqio.render_string("""
      WITH per_month AS (
        SELECT block_month, pool_name, COUNT(*) AS blocks
        FROM `${dst}.blocks`
        GROUP BY block_month, pool_name
      ),
      totals AS (
        SELECT block_month, SUM(blocks) AS total FROM per_month GROUP BY block_month
      )
      SELECT
        p.block_month, p.pool_name, p.blocks,
        p.blocks / t.total AS share
      FROM per_month p JOIN totals t USING (block_month)
      ORDER BY p.block_month DESC, share DESC
    """)
    rows = bqio.rows(sql)
    by_month = {}
    for r in rows:
        by_month.setdefault(str(r["block_month"]), []).append(r)
    keys = sorted(by_month, reverse=True)
    if months:
        keys = keys[:months]
    for month in sorted(keys):
        print(f"\n{month}")
        for r in by_month[month][:top]:
            bar = "#" * int(round(r["share"] * 60))
            print(f"  {r['pool_name']:<18s} {r['share'] * 100:5.1f}%  "
                  f"{r['blocks']:>4d}  {bar}")


def attribution_quality():
    sql = bqio.render_string("""
      SELECT
        pool_source,
        COUNT(*) AS blocks,
        COUNT(*) / SUM(COUNT(*)) OVER () AS share
      FROM `${dst}.blocks`
      GROUP BY pool_source
      ORDER BY blocks DESC
    """)
    print("how each block was attributed")
    for r in bqio.rows(sql):
        print(f"  {r['pool_source']:<16s} {r['blocks']:>7d}  {r['share'] * 100:5.2f}%")

    sql = bqio.render_string("""
      SELECT
        SUBSTR(REGEXP_REPLACE(
          SAFE_CONVERT_BYTES_TO_STRING(FROM_HEX(b.coinbase_param)),
          r'[^ -~]', '.'), 1, 48) AS coinbase_text,
        COUNT(*) AS blocks
      FROM `${src}.blocks` b
      JOIN `${dst}.blocks` d ON d.block_number = b.number
      WHERE d.pool_name = 'Unknown'
        AND b.timestamp_month BETWEEN DATE '${start_month}' AND DATE '${end_month}'
      GROUP BY coinbase_text
      ORDER BY blocks DESC
      LIMIT 15
    """)
    rows = bqio.rows(sql)
    if rows:
        print("\nmost common coinbase text among unattributed blocks")
        print("(a big count here is a missing tag in pools.py)")
        for r in rows:
            print(f"  {r['blocks']:>5d}  {r['coinbase_text']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--top", type=int, default=12,
                        help="pools listed per month")
    parser.add_argument("--months", type=int, default=12)
    parser.add_argument("--all", action="store_true", help="every month")
    args = parser.parse_args()

    attribution_quality()
    monthly_shares(None if args.all else args.months, args.top)


if __name__ == "__main__":
    main()
