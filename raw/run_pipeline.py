"""Run the private-blockspace pipeline.

    python run_pipeline.py --dry-run          # what each step would scan
    python run_pipeline.py --month 2023-04    # one month, end to end, ~$0.18
    python run_pipeline.py --from 05_block_floor

The source dataset is partitioned by month, and the pipeline aggregates by
month, so a normal run covers exactly one month with `--month`. Each run
merges its month into the JSON files in `out/`, which are tracked in git:
months already there stay, and a month run twice replaces itself. The
BigQuery working dataset is left in place; delete it with `delete_dataset.py`
once the local files hold what you need.

Step 01 is the only step that touches the public dataset. It reads that one
month's partition, about 29 GB, and everything after it works on local tables.
The run asks before spending unless `--yes` is given.
"""

import argparse
import sys

import bqio
import config
import effective_fee
import export_results

# name, kind, what it does
STEPS = [
    ("01_tx_base", "sql", "read the public dataset once"),
    ("02_blocks", "sql", "attribute every block to a pool"),
    ("03_txs", "sql", "in-block CPFP edges and non-relayable reasons"),
    ("04a_in_package", "sql", "working set for the union-find pass"),
    ("04b_union_find", "python", "package fee rates, in Python"),
    ("04c_update_effective_fee", "sql", "write package rates onto txs"),
    ("04d_update_ancestor_limit", "sql", "the ancestor-limit non-relayable reason"),
    ("05_block_floor", "sql", "p05 effective rate per block"),
    ("05b_update_block_floor", "sql", "neighbour median floor onto blocks"),
    ("06a_block_fullness", "sql", "which blocks were full"),
    ("06b_low_fee", "sql", "low-fee test at 0.3 / 0.5 / 0.7"),
    ("07_revenue_bands", "sql", "low-fee transactions and their bands"),
    ("07b_monthly_summary", "sql", "the monthly answer"),
    ("07c_pool_summary", "sql", "the same answer per pool"),
    ("08_sensitivity", "sql", "the 3x3 threshold grid"),
]

CHECKS = [
    ("transactions with no effective fee rate",
     "SELECT COUNT(*) FROM `${dst}.txs` "
     "WHERE NOT is_coinbase AND effective_fee_rate IS NULL", 0),
    ("packages whose members disagree on the price",
     "SELECT COUNT(*) FROM ("
     "  SELECT package_id, block_number FROM `${dst}.txs` "
     "  WHERE package_id IS NOT NULL "
     "  GROUP BY package_id, block_number "
     "  HAVING COUNT(DISTINCT effective_fee_rate) > 1)", 0),
    ("blocks with a floor but no percentile row",
     "SELECT COUNT(*) FROM `${dst}.blocks` b "
     "LEFT JOIN `${dst}.block_percentiles` p USING (block_number) "
     "WHERE b.floor_fee_rate IS NOT NULL AND p.block_number IS NULL", 0),
    ("low-fee transactions that are non-relayable",
     "SELECT COUNT(*) FROM `${dst}.txs` "
     "WHERE low_fee_70 AND is_nonrelayable", 0),
    ("transactions whose is_nonrelayable disagrees with its reasons",
     "SELECT COUNT(*) FROM `${dst}.txs` WHERE is_nonrelayable != ("
     "  nonrelay_nonstandard_script OR nonrelay_bare_multisig OR nonrelay_op_return"
     "  OR nonrelay_multi_op_return OR nonrelay_dust OR nonrelay_version OR nonrelay_truc"
     "  OR nonrelay_oversized OR nonrelay_undersized OR nonrelay_scriptsig_size"
     "  OR nonrelay_scriptsig_nonpush OR nonrelay_sub_minrelay"
     "  OR nonrelay_ancestor_limit)", 0),
    ("non-relayable coinbase transactions",
     "SELECT COUNT(*) FROM `${dst}.txs` "
     "WHERE is_coinbase AND is_nonrelayable", 0),
]


def sql_name(step):
    return f"{step}.sql"


def select_steps(args):
    names = [s[0] for s in STEPS]
    if args.only:
        wanted = set(args.only.split(","))
        unknown = wanted - set(names)
        if unknown:
            sys.exit(f"unknown step(s): {', '.join(sorted(unknown))}")
        return [s for s in STEPS if s[0] in wanted]
    start = 0
    if getattr(args, "from_step", None):
        if args.from_step not in names:
            sys.exit(f"unknown step: {args.from_step}")
        start = names.index(args.from_step)
    return STEPS[start:]


def dry_run(steps):
    total = 0
    print(f"window {config.START_DATE} .. {config.END_DATE}")
    for name, kind, what in steps:
        if kind != "sql":
            print(f"  {name:26s} python  {what}")
            continue
        try:
            scanned = bqio.dry_run(bqio.render(sql_name(name)))
        except Exception as exc:  # a step whose input table is not built yet
            first = str(exc).split("\n")[0]
            print(f"  {name:26s} needs an earlier step ({first[:60]})")
            continue
        total += scanned
        print(f"  {name:26s} {bqio.human_bytes(scanned):>10s}  "
              f"${bqio.usd(scanned):6.2f}  {what}")
    print(f"  {'total':26s} {bqio.human_bytes(total):>10s}  "
          f"${bqio.usd(total):6.2f}")
    return total


def run_checks():
    print("\nchecks")
    ok = True
    for label, sql, expected in CHECKS:
        got = bqio.scalar(bqio.render_string(sql))
        mark = "ok " if got == expected else "FAIL"
        if got != expected:
            ok = False
        print(f"  [{mark}] {label}: {got} (expected {expected})")
    return ok


def headline():
    sql = bqio.render_string("""
      SELECT
        SUM(low_fee_vbytes_50) AS vbytes_50,
        SAFE_DIVIDE(SUM(low_fee_vbytes_50), SUM(full_block_vbytes)) AS share_50,
        SUM(lower_band_btc_50) AS lower_btc,
        SUM(upper_band_btc_50) AS upper_btc,
        SUM(low_fee_vbytes_30) AS vbytes_30,
        SUM(low_fee_vbytes_70) AS vbytes_70
      FROM `${dst}.monthly_summary`
    """)
    row = bqio.rows(sql)[0]
    print("\nheadline, sensitivity 0.5")
    if not row["vbytes_50"]:
        print("  no low-fee space in this window")
        return
    print(f"  low-fee space      {row['vbytes_50'] / 1e9:,.2f} GvB "
          f"({(row['share_50'] or 0) * 100:.2f}% of space in full blocks)")
    print(f"  range across 0.3-0.7  {row['vbytes_30'] / 1e9:,.2f} - "
          f"{row['vbytes_70'] / 1e9:,.2f} GvB")
    print(f"  value              {row['lower_btc']:,.2f} - "
          f"{row['upper_btc']:,.2f} BTC")
    print("  read low_fee_sensitivity before quoting any of this")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="report bytes per step and stop")
    parser.add_argument("--month", metavar="YYYY-MM",
                        help="run one month end to end, then merge it into out/")
    parser.add_argument("--from", dest="from_step", metavar="STEP",
                        help="start at this step")
    parser.add_argument("--only", metavar="STEP[,STEP]",
                        help="run only these steps")
    parser.add_argument("--yes", action="store_true",
                        help="do not ask before spending")
    parser.add_argument("--skip-checks", action="store_true")
    args = parser.parse_args()

    if args.month:
        config.set_month(args.month)
        print(f"month run: {config.START_DATE} .. {config.END_DATE}")

    steps = select_steps(args)
    bqio.ensure_dataset()

    if args.dry_run:
        dry_run(steps)
        return

    estimate = None
    if any(s[0] == "01_tx_base" for s in steps):
        estimate = bqio.dry_run(bqio.render("01_tx_base.sql"))
        print(f"step 01 will scan {bqio.human_bytes(estimate)} "
              f"(about ${bqio.usd(estimate):.2f})")
        if not args.yes and not bqio.confirm("run it?"):
            sys.exit("stopped; nothing was run")

    for name, kind, what in steps:
        print(f"\n[{name}] {what}")
        if kind == "sql":
            bqio.run_file(sql_name(name), label=name)
        elif name == "04b_union_find":
            effective_fee.run(effective_fee.BigQuerySource(),
                              effective_fee.BigQueryWriter())

    if not args.skip_checks:
        run_checks()
    headline()

    if args.month:
        print(f"\nmerging {args.month} into {config.OUT_DIR}/")
        export_results.export_month(config.OUT_DIR)
        print(f"\ndone. delete the BigQuery working dataset when ready:"
              f"\n  python delete_dataset.py")


if __name__ == "__main__":
    main()
