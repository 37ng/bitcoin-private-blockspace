"""Pull the result tables out of BigQuery and write them to files.

Writes to `${OUT_DIR}` (default `out/`):

    monthly_summary.csv      per month: flagged space, share, value bands
    pool_summary.csv         the same per pool
    low_fee_sensitivity.csv   the 3x3 threshold grid
    flagged_txs_sample.csv   the 5,000 largest flagged transactions
    headline.json            the numbers quoted in the write-up
    summary.md               a readable digest of all of the above

`export_month()` is what `run_pipeline.py --month` calls after a one-month
run. The BigQuery working dataset holds only that month's tables (each
pipeline step is a `CREATE OR REPLACE TABLE`), so it merges the fresh month
into the existing local files instead of overwriting them: `monthly_summary`
and `pool_summary` are keyed by month, `low_fee_sensitivity` sums across
months per grid cell, and `flagged_txs_sample` keeps the largest 5,000 seen
across all runs so far. The BigQuery dataset can then be dropped with
`delete_dataset.py` before the next month runs.
"""

import argparse
import json
import os

import pandas as pd

import bqio
import config

TABLES = {
    "monthly_summary": "SELECT * FROM `${dst}.monthly_summary` ORDER BY block_month",
    "pool_summary": "SELECT * FROM `${dst}.pool_summary` "
                    "ORDER BY block_month, flagged_vbytes_50 DESC",
    "low_fee_sensitivity": "SELECT * FROM `${dst}.low_fee_sensitivity` "
                          "ORDER BY sensitivity, full_weight",
    "flagged_txs_sample": "SELECT * FROM `${dst}.flagged_txs` "
                          "WHERE low_fee_50 ORDER BY upper_band_sats DESC LIMIT 5000",
}


def export_table(name, sql, out_dir):
    df = bqio.client().query(bqio.render_string(sql)).result().to_dataframe()
    path = os.path.join(out_dir, f"{name}.csv")
    df.to_csv(path, index=False)
    print(f"  {path}  {len(df)} rows")
    return df


def headline_numbers(monthly, sensitivity):
    flagged = monthly[f"flagged_vbytes_{sensitivity}"].sum()
    full = monthly["full_block_vbytes"].sum()
    return {
        "window_start": str(monthly["block_month"].min()),
        "window_end": str(monthly["block_month"].max()),
        "sensitivity": f"0.{sensitivity}",
        "flagged_txs": int(monthly[f"flagged_txs_{sensitivity}"].sum()),
        "flagged_vbytes": int(flagged),
        "flagged_gvb": round(float(flagged) / 1e9, 3),
        "full_block_vbytes": int(full),
        "share_of_full_block_space": round(float(flagged) / float(full), 6)
        if full else None,
        "lower_band_btc": round(float(monthly[f"lower_band_btc_{sensitivity}"].sum()), 4),
        "upper_band_btc": round(float(monthly[f"upper_band_btc_{sensitivity}"].sum()), 4),
        "nonrelayable_txs": int(monthly["nonrelayable_txs"].sum()),
        "nonrelayable_vbytes": int(monthly["nonrelayable_vbytes"].sum()),
    }


def write_summary(out_dir, monthly, sensitivity_grid, pools):
    head = {s: headline_numbers(monthly, s) for s in ("30", "50", "70")}
    mid = head["50"]
    lines = [
        "# Private blockspace, "
        f"{mid['window_start']} to {mid['window_end']}",
        "",
        "Block space that changed hands below the public price, in blocks that "
        "were full at the time. Non-relayable traffic is excluded from the "
        "count: it never entered the public auction, so its price says nothing "
        "about a discount.",
        "",
        "## Headline (sensitivity 0.5)",
        "",
        f"- flagged transactions: {mid['flagged_txs']:,}",
        f"- flagged space: {mid['flagged_gvb']:,.2f} GvB, "
        f"{mid['share_of_full_block_space'] * 100:.2f}% of space in full blocks"
        if mid["share_of_full_block_space"] is not None else "- flagged space: n/a",
        f"- value: {mid['lower_band_btc']:,.2f} BTC (lower band) to "
        f"{mid['upper_band_btc']:,.2f} BTC (upper band)",
        "",
        "## Across sensitivities",
        "",
        "| sensitivity | flagged txs | flagged GvB | share of full blocks | "
        "lower band BTC | upper band BTC |",
        "|---|---|---|---|---|---|",
    ]
    for s in ("30", "50", "70"):
        h = head[s]
        share = (f"{h['share_of_full_block_space'] * 100:.2f}%"
                 if h["share_of_full_block_space"] is not None else "n/a")
        lines.append(
            f"| 0.{s} | {h['flagged_txs']:,} | {h['flagged_gvb']:,.2f} | {share} | "
            f"{h['lower_band_btc']:,.2f} | {h['upper_band_btc']:,.2f} |")

    lines += ["", "## Threshold grid", "",
              "| sensitivity | full weight | flagged GvB | share | lower BTC | upper BTC |",
              "|---|---|---|---|---|---|"]
    for _, r in sensitivity_grid.iterrows():
        share = f"{r['flagged_share'] * 100:.2f}%" if r["flagged_share"] == r["flagged_share"] else "n/a"
        lines.append(
            f"| {r['sensitivity']} | {int(r['full_weight']):,} | "
            f"{r['flagged_vbytes'] / 1e9:,.2f} | {share} | "
            f"{r['lower_band_btc']:,.2f} | {r['upper_band_btc']:,.2f} |")

    if len(pools):
        top = (pools.groupby("pool_name")[["flagged_vbytes_50", "vbytes"]]
               .sum().sort_values("flagged_vbytes_50", ascending=False).head(12))
        lines += ["", "## By pool", "",
                  "| pool | flagged GvB | flagged share of its own space |",
                  "|---|---|---|"]
        for pool, r in top.iterrows():
            share = r["flagged_vbytes_50"] / r["vbytes"] if r["vbytes"] else 0
            lines.append(f"| {pool} | {r['flagged_vbytes_50'] / 1e9:,.2f} | "
                         f"{share * 100:.3f}% |")

    lines += [
        "",
        "## Reading this",
        "",
        "- The two bands are not a measurement of a private payment. That "
        "happens off chain and leaves no record. The lower band is what the "
        "buyer did not pay against the cheapest public price in the block; the "
        "upper band is what the same space would have fetched in the middle of "
        "the public auction.",
        "- A number that moves by an order of magnitude across the threshold "
        "grid is a statement about the cut-offs, not about the chain.",
        "- Per-pool rows depend on coinbase tag attribution. Run "
        "`sanity_check.py` and compare against a public hashrate chart before "
        "quoting any of them.",
    ]

    path = os.path.join(out_dir, "summary.md")
    with open(path, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"  {path}")

    path = os.path.join(out_dir, "headline.json")
    with open(path, "w") as fh:
        json.dump(head, fh, indent=2)
    print(f"  {path}")


def _load(out_dir, name):
    path = os.path.join(out_dir, f"{name}.csv")
    return pd.read_csv(path) if os.path.exists(path) else None


def _merge_by_key(existing, fresh, keys):
    """Replace rows sharing a key (this month, re-run) and keep the rest."""
    if existing is None or existing.empty:
        return fresh.sort_values(keys).reset_index(drop=True)
    fresh_keys = fresh.set_index(keys).index
    kept = existing[~existing.set_index(keys).index.isin(fresh_keys)]
    return (pd.concat([kept, fresh], ignore_index=True)
            .sort_values(keys).reset_index(drop=True))


def _merge_sensitivity(existing, fresh):
    """Grid cells sum across disjoint months; the share is recomputed."""
    keys = ["sensitivity", "full_weight"]
    sum_cols = ["flagged_txs", "flagged_vbytes", "full_block_vbytes",
                "lower_band_btc", "upper_band_btc"]
    if existing is None or existing.empty:
        combined = fresh[keys + sum_cols].copy()
    else:
        combined = (pd.concat([existing[keys + sum_cols], fresh[keys + sum_cols]])
                    .groupby(keys, as_index=False)[sum_cols].sum())
    combined["flagged_share"] = combined["flagged_vbytes"] / combined["full_block_vbytes"]
    return combined.sort_values(keys).reset_index(drop=True)


def _merge_top_k(existing, fresh, sort_col, k=5000):
    combined = fresh if existing is None or existing.empty else pd.concat([existing, fresh])
    return (combined.sort_values(sort_col, ascending=False)
            .head(k).reset_index(drop=True))


def export_month(out_dir):
    """Fetch the current (single-month) tables and merge them into out_dir."""
    os.makedirs(out_dir, exist_ok=True)
    fresh = {name: bqio.client().query(bqio.render_string(sql)).result().to_dataframe()
             for name, sql in TABLES.items()}

    merged = {
        "monthly_summary": _merge_by_key(
            _load(out_dir, "monthly_summary"), fresh["monthly_summary"], ["block_month"]),
        "pool_summary": _merge_by_key(
            _load(out_dir, "pool_summary"), fresh["pool_summary"],
            ["block_month", "pool_name"]),
        "low_fee_sensitivity": _merge_sensitivity(
            _load(out_dir, "low_fee_sensitivity"), fresh["low_fee_sensitivity"]),
        "flagged_txs_sample": _merge_top_k(
            _load(out_dir, "flagged_txs_sample"), fresh["flagged_txs_sample"],
            "upper_band_sats"),
    }
    for name, df in merged.items():
        path = os.path.join(out_dir, f"{name}.csv")
        df.to_csv(path, index=False)
        print(f"  {path}  {len(df)} rows")

    write_summary(out_dir, merged["monthly_summary"],
                  merged["low_fee_sensitivity"], merged["pool_summary"])


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=config.OUT_DIR)
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    print(f"writing to {args.out}/")
    frames = {name: export_table(name, sql, args.out)
              for name, sql in TABLES.items()}
    write_summary(args.out, frames["monthly_summary"],
                  frames["low_fee_sensitivity"], frames["pool_summary"])


if __name__ == "__main__":
    main()
