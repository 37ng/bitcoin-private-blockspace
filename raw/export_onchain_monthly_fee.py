import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "utils"))

import bqio
import config

FILENAME = "onchain_monthly_fee.json"
SATS = 100_000_000


def monthly_rows():
    return bqio.rows(
        f"SELECT FORMAT_DATE('%Y-%m', block_month) AS month, txs, blocks, "
        f"fee_sats, vbytes, fee_rate_sat_vb "
        f"FROM `{config.dst()}.onchain_monthly_fee` ORDER BY block_month")


def build(rows):
    return [{
        "month": r["month"],
        "txs": int(r["txs"]),
        "blocks": int(r["blocks"]),
        "fee_sats": int(r["fee_sats"] or 0),
        "fee_btc": round(int(r["fee_sats"] or 0) / SATS, 8),
        "vbytes": int(r["vbytes"] or 0),
        "fee_rate_sat_vb": round(float(r["fee_rate_sat_vb"]), 3)
        if r["fee_rate_sat_vb"] is not None else None,
    } for r in rows]


def read(path):
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        return json.load(fh).get("months", [])


def merge(existing, fresh):
    by_month = {m["month"]: m for m in existing}
    for m in fresh:
        by_month[m["month"]] = m
    return [by_month[k] for k in sorted(by_month)]


def payload(months):
    out = {
        "source": "bigquery-public-data.crypto_bitcoin.transactions",
        "unit": "one calendar month",
        "note": ("Transaction fees paid inside blocks, summed per month. "
                 "Coinbase transactions are excluded, so this is fee revenue "
                 "only, without the subsidy."),
        "months": months,
    }
    if months:
        out["first_month"] = months[0]["month"]
        out["last_month"] = months[-1]["month"]
        out["total_fee_btc"] = round(
            sum(m["fee_sats"] for m in months) / SATS, 8)
    return out


def write(data, path):
    text = json.dumps(data, indent=2) + "\n"
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


def report(months):
    print(f"\n=== {len(months)} months ===")
    print(f"{'month':<9}{'blocks':>9}{'txs':>12}{'fee BTC':>14}{'sat/vB':>10}")
    for m in months:
        print(f"{m['month']:<9}{m['blocks']:>9,d}{m['txs']:>12,d}"
              f"{m['fee_btc']:>14.4f}{m['fee_rate_sat_vb'] or 0:>10.1f}")
    total = sum(m["fee_sats"] for m in months) / SATS
    print(f"{'total':<9}{'':>9}{'':>12}{total:>14.4f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=os.path.join(config.DATA_DIR, FILENAME))
    args = parser.parse_args()

    fresh = build(monthly_rows())
    if not fresh:
        print("the onchain_monthly_fee table is empty; nothing to publish")
        return 1

    months = merge(read(args.out), fresh)
    report(months)
    write(payload(months), args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
