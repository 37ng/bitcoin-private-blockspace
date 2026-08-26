"""Pull the result tables out of BigQuery and write them to files.

Writes to `${OUT_DIR}` (default `out/`):

    monthly_summary.csv      per month: flagged space, share, value bands
    pool_summary.csv         the same per pool
    flag_a_sensitivity.csv   the 3x3 threshold grid
    flagged_txs_sample.csv   the 5,000 largest flagged transactions
    headline.json            the numbers quoted in the write-up
    summary.md               a readable digest of all of the above

`visualize.py` reads these files, not BigQuery, so charts can be redrawn
without touching the warehouse.
"""

import argparse
import json
import os

import bqio
import config

TABLES = {
    "monthly_summary": "SELECT * FROM `${dst}.monthly_summary` ORDER BY block_month",
    "pool_summary": "SELECT * FROM `${dst}.pool_summary` "
                    "ORDER BY block_month, flagged_vbytes_50 DESC",
    "flag_a_sensitivity": "SELECT * FROM `${dst}.flag_a_sensitivity` "
                          "ORDER BY sensitivity, full_weight",
    "flagged_txs_sample": "SELECT * FROM `${dst}.flagged_txs` "
                          "WHERE flag_a_50 ORDER BY upper_band_sats DESC LIMIT 5000",
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
                  frames["flag_a_sensitivity"], frames["pool_summary"])


if __name__ == "__main__":
    main()
