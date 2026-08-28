import argparse
import datetime
import decimal
import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "utils"))

import bqio
import config

TABLES = {
    "monthly_summary": "SELECT * FROM `${dst}.monthly_summary` ORDER BY block_month",
    "pool_summary": "SELECT * FROM `${dst}.pool_summary` "
                    "ORDER BY block_month, low_fee_vbytes_50 DESC",
    "low_fee_sensitivity": "SELECT * FROM `${dst}.low_fee_sensitivity` "
                          "ORDER BY block_month, sensitivity, full_weight",
    "low_fee_txs_sample": "SELECT * FROM `${dst}.low_fee_txs` "
                          "WHERE low_fee_50 ORDER BY upper_band_sats DESC LIMIT 5000",
}

# How each table is sorted on disk, so a re-run produces a small git diff.
SORT_KEYS = {
    "monthly_summary": ["block_month"],
    "pool_summary": ["block_month", "pool_name"],
    "low_fee_sensitivity": ["block_month", "sensitivity", "full_weight"],
}

# Rows kept in `low_fee_txs_sample.json`, across every month exported so far.
SAMPLE_SIZE = 5000

# The month column every result table carries. It is what makes a re-run
# replace rather than accumulate.
MONTH = "block_month"


# --- JSON on disk --------------------------------------------------------

def _is_missing(value):
    if isinstance(value, (list, tuple, dict, set)):
        return False
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _json_safe(value):
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if _is_missing(value):
        return None
    if isinstance(value, (pd.Timestamp, datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if hasattr(value, "item"):  # numpy / pandas scalar
        return _json_safe(value.item())
    return value


def read_json(out_dir, name):
    path = os.path.join(out_dir, f"{name}.json")
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        return pd.DataFrame(json.load(fh))


def write_json(out_dir, name, df):
    path = os.path.join(out_dir, f"{name}.json")
    records = [{k: _json_safe(v) for k, v in row.items()}
               for row in df.to_dict(orient="records")]
    with open(path, "w") as fh:
        json.dump(records, fh, indent=2)
        fh.write("\n")
    print(f"  {path}  {len(records)} rows")


def normalise(df):
    return pd.DataFrame([{k: _json_safe(v) for k, v in row.items()}
                         for row in df.to_dict(orient="records")],
                        columns=list(df.columns))


# --- merging -------------------------------------------------------------

def months_covered(monthly):
    if monthly is None or monthly.empty:
        return set()
    return set(monthly[MONTH])


def merge_months(existing, fresh, months, sort_keys):
    kept = None
    if existing is not None and not existing.empty and MONTH in existing.columns:
        kept = existing[~existing[MONTH].isin(months)]
    if kept is None or kept.empty:
        combined = fresh
    else:
        combined = pd.concat([kept, fresh], ignore_index=True)
    if combined.empty:
        return combined.reset_index(drop=True)
    return combined.sort_values(sort_keys).reset_index(drop=True)


def merge_sample(existing, fresh, months, sort_col, k=SAMPLE_SIZE):
    combined = merge_months(existing, fresh, months, [sort_col])
    if combined.empty:
        return combined
    return (combined.sort_values(sort_col, ascending=False)
            .head(k).reset_index(drop=True))


def sensitivity_totals(grid):
    keys = ["sensitivity", "full_weight"]
    sum_cols = ["low_fee_txs", "low_fee_vbytes", "full_block_vbytes",
                "lower_band_btc", "upper_band_btc"]
    if grid is None or grid.empty:
        return pd.DataFrame(columns=keys + sum_cols + ["low_fee_share"])
    totals = grid.groupby(keys, as_index=False)[sum_cols].sum()
    totals["low_fee_share"] = totals["low_fee_vbytes"] / totals["full_block_vbytes"]
    return totals.sort_values(keys).reset_index(drop=True)


# --- the write-up numbers ------------------------------------------------

def headline_numbers(monthly, sensitivity):
    low_fee = monthly[f"low_fee_vbytes_{sensitivity}"].sum()
    full = monthly["full_block_vbytes"].sum()
    return {
        "window_start": str(monthly[MONTH].min()),
        "window_end": str(monthly[MONTH].max()),
        "months": int(monthly[MONTH].nunique()),
        "sensitivity": f"0.{sensitivity}",
        "low_fee_txs": int(monthly[f"low_fee_txs_{sensitivity}"].sum()),
        "low_fee_vbytes": int(low_fee),
        "low_fee_gvb": round(float(low_fee) / 1e9, 3),
        "full_block_vbytes": int(full),
        "share_of_full_block_space": round(float(low_fee) / float(full), 6)
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
        f"Covers {mid['months']} month(s), added one run at a time.",
        "",
        "## Headline (sensitivity 0.5)",
        "",
        f"- low-fee transactions: {mid['low_fee_txs']:,}",
        f"- low-fee space: {mid['low_fee_gvb']:,.2f} GvB, "
        f"{mid['share_of_full_block_space'] * 100:.2f}% of space in full blocks"
        if mid["share_of_full_block_space"] is not None else "- low-fee space: n/a",
        f"- value: {mid['lower_band_btc']:,.2f} BTC (lower band) to "
        f"{mid['upper_band_btc']:,.2f} BTC (upper band)",
        "",
        "## Across sensitivities",
        "",
        "| sensitivity | low-fee txs | low-fee GvB | share of full blocks | "
        "lower band BTC | upper band BTC |",
        "|---|---|---|---|---|---|",
    ]
    for s in ("30", "50", "70"):
        h = head[s]
        share = (f"{h['share_of_full_block_space'] * 100:.2f}%"
                 if h["share_of_full_block_space"] is not None else "n/a")
        lines.append(
            f"| 0.{s} | {h['low_fee_txs']:,} | {h['low_fee_gvb']:,.2f} | {share} | "
            f"{h['lower_band_btc']:,.2f} | {h['upper_band_btc']:,.2f} |")

    lines += ["", "## Threshold grid", "",
              "| sensitivity | full weight | low-fee GvB | share | lower BTC | upper BTC |",
              "|---|---|---|---|---|---|"]
    for _, r in sensitivity_grid.iterrows():
        share = f"{r['low_fee_share'] * 100:.2f}%" if r["low_fee_share"] == r["low_fee_share"] else "n/a"
        lines.append(
            f"| {r['sensitivity']} | {int(r['full_weight']):,} | "
            f"{r['low_fee_vbytes'] / 1e9:,.2f} | {share} | "
            f"{r['lower_band_btc']:,.2f} | {r['upper_band_btc']:,.2f} |")

    if len(pools):
        top = (pools.groupby("pool_name")[["low_fee_vbytes_50", "vbytes"]]
               .sum().sort_values("low_fee_vbytes_50", ascending=False).head(12))
        lines += ["", "## By pool", "",
                  "| pool | low-fee GvB | low-fee share of its own space |",
                  "|---|---|---|"]
        for pool, r in top.iterrows():
            share = r["low_fee_vbytes_50"] / r["vbytes"] if r["vbytes"] else 0
            lines.append(f"| {pool} | {r['low_fee_vbytes_50'] / 1e9:,.2f} | "
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
        fh.write("\n")
    print(f"  {path}")


# --- the export ----------------------------------------------------------

def fetch():
    return {name: normalise(
        bqio.client().query(bqio.render_string(sql)).result().to_dataframe())
        for name, sql in TABLES.items()}


def merge_into(out_dir, fresh, replace=False):
    os.makedirs(out_dir, exist_ok=True)
    months = months_covered(fresh["monthly_summary"])
    if not months:
        print("  the working dataset holds no months; nothing to merge")
        return None

    def on_disk(name):
        return None if replace else read_json(out_dir, name)

    merged = {
        name: merge_months(on_disk(name), fresh[name], months, keys)
        for name, keys in SORT_KEYS.items()
    }
    merged["low_fee_txs_sample"] = merge_sample(
        on_disk("low_fee_txs_sample"), fresh["low_fee_txs_sample"], months,
        "upper_band_sats")

    for name in TABLES:
        write_json(out_dir, name, merged[name])

    write_summary(out_dir, merged["monthly_summary"],
                  sensitivity_totals(merged["low_fee_sensitivity"]),
                  merged["pool_summary"])
    return merged


def export_month(out_dir, replace=False):
    return merge_into(out_dir, fetch(), replace=replace)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=config.OUT_DIR)
    parser.add_argument("--replace", action="store_true",
                        help="ignore what is already in --out and write only "
                             "the months the working dataset holds")
    args = parser.parse_args()

    print(f"writing to {args.out}/")
    export_month(args.out, replace=args.replace)


if __name__ == "__main__":
    main()
