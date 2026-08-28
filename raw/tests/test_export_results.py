import datetime
import decimal
import json
import os

import pandas as pd
import pytest

import export_results as ex


def monthly(month, low_fee_vbytes=100, full=1000):
    row = {
        "block_month": month,
        "all_txs": 10,
        "all_vbytes": full * 2,
        "full_block_vbytes": full,
        "nonrelayable_txs": 1,
        "nonrelayable_vbytes": 5,
    }
    for s in ("30", "50", "70"):
        row[f"low_fee_txs_{s}"] = 2
        row[f"low_fee_vbytes_{s}"] = low_fee_vbytes
        row[f"lower_band_btc_{s}"] = 1.0
        row[f"upper_band_btc_{s}"] = 3.0
    return row


def pools(month, name="PoolA"):
    return {"block_month": month, "pool_name": name, "blocks": 4,
            "vbytes": 800, "full_block_vbytes": 800, "low_fee_txs_50": 2,
            "low_fee_vbytes_30": 50, "low_fee_vbytes_50": 100,
            "low_fee_vbytes_70": 150, "low_fee_share_of_full_50": 0.125,
            "lower_band_btc_50": 1.0, "upper_band_btc_50": 3.0}


def grid(month, low_fee_vbytes=100):
    return {"block_month": month, "sensitivity": 0.5, "full_weight": 3900000,
            "low_fee_txs": 2, "low_fee_vbytes": low_fee_vbytes,
            "full_block_vbytes": 1000, "low_fee_share": low_fee_vbytes / 1000,
            "lower_band_btc": 1.0, "upper_band_btc": 3.0}


def sample(month, tx_hash, upper=500):
    return {"tx_hash": tx_hash, "block_month": month, "block_number": 1,
            "pool_name": "PoolA", "virtual_size": 200, "low_fee_50": True,
            "lower_band_sats": 100, "upper_band_sats": upper}


def fetched(month, **kw):
    return {
        "monthly_summary": pd.DataFrame([monthly(month, **kw)]),
        "pool_summary": pd.DataFrame([pools(month)]),
        "low_fee_sensitivity": pd.DataFrame([grid(month)]),
        "low_fee_txs_sample": pd.DataFrame([sample(month, f"{month}-tx")]),
    }


def load(out_dir, name):
    with open(os.path.join(out_dir, f"{name}.json")) as fh:
        return json.load(fh)


# --- JSON on disk --------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (datetime.date(2023, 4, 1), "2023-04-01"),
    (decimal.Decimal("1.5"), 1.5),
    (float("nan"), None),
    (None, None),
    (pd.NaT, None),
    ("PoolA", "PoolA"),
    (True, True),
])
def test_json_safe_values(value, expected):
    assert ex._json_safe(value) == expected


def test_dates_survive_the_round_trip_as_keys(tmp_path):
    fresh = ex.normalise(pd.DataFrame([{"block_month": datetime.date(2023, 4, 1)}]))
    ex.write_json(str(tmp_path), "t", fresh)
    assert set(ex.read_json(str(tmp_path), "t")["block_month"]) == \
        set(fresh["block_month"])


def test_read_json_is_none_when_the_file_is_not_there(tmp_path):
    assert ex.read_json(str(tmp_path), "monthly_summary") is None


# --- the merge -----------------------------------------------------------

def test_a_second_month_is_added_not_substituted(tmp_path):
    out = str(tmp_path)
    ex.merge_into(out, fetched("2023-04"))
    ex.merge_into(out, fetched("2023-05"))

    months = [r["block_month"] for r in load(out, "monthly_summary")]
    assert months == ["2023-04", "2023-05"]
    assert len(load(out, "pool_summary")) == 2
    assert len(load(out, "low_fee_sensitivity")) == 2
    assert {r["tx_hash"] for r in load(out, "low_fee_txs_sample")} == \
        {"2023-04-tx", "2023-05-tx"}


def test_rerunning_a_month_replaces_it(tmp_path):
    out = str(tmp_path)
    ex.merge_into(out, fetched("2023-04", low_fee_vbytes=100))
    ex.merge_into(out, fetched("2023-04", low_fee_vbytes=250))

    rows = load(out, "monthly_summary")
    assert len(rows) == 1
    assert rows[0]["low_fee_vbytes_50"] == 250
    assert len(load(out, "low_fee_sensitivity")) == 1
    assert len(load(out, "low_fee_txs_sample")) == 1


def test_a_rerun_does_not_double_the_sensitivity_grid(tmp_path):
    out = str(tmp_path)
    ex.merge_into(out, fetched("2023-04"))
    after_one = ex.sensitivity_totals(ex.read_json(out, "low_fee_sensitivity"))
    ex.merge_into(out, fetched("2023-04"))
    after_two = ex.sensitivity_totals(ex.read_json(out, "low_fee_sensitivity"))

    assert list(after_one["low_fee_vbytes"]) == list(after_two["low_fee_vbytes"])


def test_the_grid_sums_across_months_and_recomputes_the_share(tmp_path):
    out = str(tmp_path)
    ex.merge_into(out, fetched("2023-04"))
    ex.merge_into(out, fetched("2023-05"))

    totals = ex.sensitivity_totals(ex.read_json(out, "low_fee_sensitivity"))
    assert len(totals) == 1                       # one cell, two months
    assert totals.loc[0, "low_fee_vbytes"] == 200
    assert totals.loc[0, "full_block_vbytes"] == 2000
    # the share is recomputed from the sums, never itself summed
    assert totals.loc[0, "low_fee_share"] == pytest.approx(0.1)


def test_replace_drops_the_months_on_disk(tmp_path):
    out = str(tmp_path)
    ex.merge_into(out, fetched("2023-04"))
    ex.merge_into(out, fetched("2023-05"), replace=True)
    assert [r["block_month"] for r in load(out, "monthly_summary")] == ["2023-05"]


def test_an_empty_dataset_leaves_the_files_alone(tmp_path):
    out = str(tmp_path)
    ex.merge_into(out, fetched("2023-04"))
    before = load(out, "monthly_summary")

    empty = {name: df.iloc[0:0] for name, df in fetched("2023-04").items()}
    assert ex.merge_into(out, empty) is None
    assert load(out, "monthly_summary") == before


def test_the_sample_keeps_the_largest_across_months():
    on_disk = pd.DataFrame([sample("2023-04", "big", upper=900),
                            sample("2023-04", "small", upper=1)])
    fresh = pd.DataFrame([sample("2023-05", "mid", upper=500)])

    kept = ex.merge_sample(on_disk, fresh, {"2023-05"}, "upper_band_sats", k=2)
    assert list(kept["tx_hash"]) == ["big", "mid"]


def test_rerunning_a_month_drops_its_old_sample_rows():
    on_disk = pd.DataFrame([sample("2023-04", "stale", upper=900)])
    fresh = pd.DataFrame([sample("2023-04", "fresh", upper=500)])

    kept = ex.merge_sample(on_disk, fresh, {"2023-04"}, "upper_band_sats")
    assert list(kept["tx_hash"]) == ["fresh"]


# --- the derived files ---------------------------------------------------

def test_the_summary_covers_every_month_on_disk(tmp_path):
    out = str(tmp_path)
    ex.merge_into(out, fetched("2023-04"))
    ex.merge_into(out, fetched("2023-05"))

    with open(os.path.join(out, "headline.json")) as fh:
        head = json.load(fh)
    assert head["50"]["window_start"] == "2023-04"
    assert head["50"]["window_end"] == "2023-05"
    assert head["50"]["months"] == 2
    assert head["50"]["low_fee_vbytes"] == 200

    with open(os.path.join(out, "summary.md")) as fh:
        assert "2023-04 to 2023-05" in fh.read()


def test_every_table_lands_as_json(tmp_path):
    out = str(tmp_path)
    ex.merge_into(out, fetched("2023-04"))
    written = sorted(os.listdir(out))
    assert written == ["headline.json", "low_fee_sensitivity.json",
                       "low_fee_txs_sample.json", "monthly_summary.json",
                       "pool_summary.json", "summary.md"]
