import calendar
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import export_accelerations as ex


def ts(text):
    return calendar.timegm(tuple(int(p) for p in text.split("-"))
                           + (0, 0, 0, 0, 1, 0))


# --- month boundaries ----------------------------------------------------

def test_month_bounds_span_exactly_the_month():
    assert ex.month_bounds("2024-02") == (ts("2024-02-01"), ts("2024-03-01"))


def test_month_bounds_roll_over_the_year():
    assert ex.month_bounds("2024-12") == (ts("2024-12-01"), ts("2025-01-01"))


# --- the publishing rule -------------------------------------------------

def test_a_month_inside_the_run_is_complete():
    assert ex.is_complete("2024-02", ts("2024-01-01"), ts("2024-04-01"))


def test_a_month_the_run_stops_inside_is_not_complete():
    assert not ex.is_complete("2024-02", ts("2024-02-01"), ts("2024-02-20"))


def test_a_month_starting_before_the_run_is_not_complete():
    assert not ex.is_complete("2024-02", ts("2024-02-10"), ts("2024-09-01"))


def test_a_month_meeting_the_run_exactly_at_both_ends_is_complete():
    assert ex.is_complete("2024-02", ts("2024-02-01"), ts("2024-03-01"))


def test_the_newest_month_is_never_complete():
    assert not ex.is_complete("2024-08", ts("2023-01-01"), ts("2024-08-14"))


def test_last_month_is_complete_once_the_run_reaches_into_this_one():
    assert ex.is_complete("2024-07", ts("2023-01-01"), ts("2024-08-14"))


def test_an_empty_table_completes_nothing():
    assert not ex.is_complete("2024-07", None, None)


# --- why a month is held back --------------------------------------------

def test_a_month_below_the_run_says_so():
    assert ex.hold_reason("2023-05", ts("2024-01-01"),
                          ts("2024-08-01")) == "before the run"


def test_the_newest_month_is_filling():
    assert ex.hold_reason("2024-08", ts("2023-01-01"),
                          ts("2024-08-14")) == "filling"


# --- the payload ---------------------------------------------------------

def row(month, n=100, off=500_000, vsize=1000):
    return {"month": month, "n_accelerations": n, "off_chain_sats": off,
            "bid_boost_sats": 0, "on_chain_sats": 1000, "vsize": vsize,
            "off_chain_sat_vb": off / vsize, "on_chain_sat_vb": 1.0}


def test_months_outside_the_run_are_left_out_of_the_file_and_the_totals():
    payload, held = ex.build(
        [row("2023-12"), row("2024-01"), row("2024-02"), row("2024-03")],
        ts("2024-01-01"), ts("2024-03-05"))
    assert [m["month"] for m in payload["months"]] == ["2024-01", "2024-02"]
    assert {h["month"]: h["reason"] for h in held} == {
        "2023-12": "before the run", "2024-03": "filling"}
    # 2 x 500,000 sats, not 4
    assert payload["total_off_chain_btc"] == 0.01
    assert payload["total_accelerations"] == 200


def test_the_payload_has_no_timestamp_in_it():
    a, _ = ex.build([row("2024-01")], ts("2024-01-01"), ts("2024-03-01"))
    b, _ = ex.build([row("2024-01")], ts("2024-01-01"), ts("2024-03-01"))
    assert a == b
