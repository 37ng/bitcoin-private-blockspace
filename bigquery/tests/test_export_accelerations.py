"""Offline tests for the completeness rule behind the published file.

The file in `data/` is what the write-up quotes, so the only thing that must
never happen is a half-fetched month appearing in it as a finished one. That
whole guarantee rests on the span arithmetic here, which is pure, so it is
tested without BigQuery and without credentials.
"""

import calendar
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import export_accelerations as ex


def ts(text):
    """A "YYYY-MM-DD" as a unix timestamp, so spans read as dates."""
    return calendar.timegm(tuple(int(p) for p in text.split("-")) + (0, 0, 0, 0, 1, 0))


# --- merging the ledger --------------------------------------------------

def test_overlapping_runs_merge():
    """Every top-up re-reads --overlap pages, so spans overlap by design."""
    assert ex.merge_spans([(10, 20), (15, 30)]) == [(10, 30)]


def test_touching_runs_merge():
    """Two runs that met exactly leave no gap between them."""
    assert ex.merge_spans([(10, 20), (20, 30)]) == [(10, 30)]


def test_a_real_gap_survives_merging():
    assert ex.merge_spans([(10, 20), (25, 30)]) == [(10, 20), (25, 30)]


def test_spans_may_arrive_in_any_order():
    """Ranges can be backfilled newest-first, oldest-first, or at random."""
    assert ex.merge_spans([(25, 30), (10, 20), (15, 26)]) == [(10, 30)]


def test_a_span_swallowed_by_another_disappears():
    assert ex.merge_spans([(0, 100), (20, 30)]) == [(0, 100)]


# --- month boundaries ----------------------------------------------------

def test_month_bounds_span_exactly_the_month():
    assert ex.month_bounds("2024-02") == (ts("2024-02-01"), ts("2024-03-01"))


def test_month_bounds_roll_over_the_year():
    assert ex.month_bounds("2024-12") == (ts("2024-12-01"), ts("2025-01-01"))


# --- the publishing rule -------------------------------------------------

def test_a_month_read_end_to_end_is_complete():
    covered = [(ts("2024-01-01"), ts("2024-04-01"))]
    assert ex.is_covered("2024-02", covered)


def test_a_month_read_only_partway_is_not_complete():
    """The failure this whole file exists to prevent."""
    covered = [(ts("2024-02-01"), ts("2024-02-20"))]
    assert not ex.is_covered("2024-02", covered)


def test_a_month_with_a_hole_in_the_middle_is_not_complete():
    """Two runs touching both ends of a month have still not read it."""
    covered = ex.merge_spans([(ts("2024-01-01"), ts("2024-02-10")),
                              (ts("2024-02-20"), ts("2024-03-05"))])
    assert not ex.is_covered("2024-02", covered)


def test_the_current_month_is_never_complete():
    """Coverage ends when the last run started, which is inside this month.

    The rule the write-up needs -- never quote a month still filling -- falls
    out of the arithmetic, so there is no calendar special case to get wrong.
    """
    covered = [(ts("2024-01-01"), ts("2024-08-14"))]     # a run mid-August
    assert not ex.is_covered("2024-08", covered)


def test_last_month_is_complete_once_a_run_has_happened_in_this_one():
    """The normal state: top-ups running, so July is publishable in August."""
    covered = [(ts("2024-01-01"), ts("2024-08-14"))]
    assert ex.is_covered("2024-07", covered)


# --- what to fetch next --------------------------------------------------

def test_an_untouched_month_asks_for_the_whole_month():
    assert ex.gap_before("2024-02", [(ts("2024-06-01"), ts("2024-09-01"))]) == (
        ts("2024-02-01"), ts("2024-03-01"))


def test_a_month_read_from_its_start_asks_only_for_the_rest():
    covered = [(ts("2023-01-01"), ts("2024-02-20"))]
    assert ex.gap_before("2024-02", covered) == (ts("2024-02-20"),
                                                 ts("2024-03-01"))


def test_a_month_read_up_to_its_end_asks_only_for_the_front():
    covered = [(ts("2024-02-20"), ts("2024-09-01"))]
    assert ex.gap_before("2024-02", covered) == (ts("2024-02-01"),
                                                 ts("2024-02-20"))


def test_a_complete_month_asks_for_nothing():
    assert ex.gap_before("2024-02", [(ts("2024-01-01"),
                                      ts("2024-04-01"))]) is None


# --- the payload ---------------------------------------------------------

def row(month, n=100, off=500_000, vsize=1000):
    return {"month": month, "n_accelerations": n, "off_chain_sats": off,
            "bid_boost_sats": 0, "on_chain_sats": 1000, "vsize": vsize,
            "off_chain_sat_vb": off / vsize, "on_chain_sat_vb": 1.0}


def test_an_unread_month_is_left_out_of_the_file_and_the_totals():
    """A month with records but no coverage must not reach the write-up."""
    covered = [(ts("2024-01-01"), ts("2024-03-01"))]
    payload, skipped = ex.build([row("2024-01"), row("2024-02"),
                                 row("2024-03")], covered)
    assert [m["month"] for m in payload["months"]] == ["2024-01", "2024-02"]
    assert [s["month"] for s in skipped] == ["2024-03"]
    # 2 x 500,000 sats, not 3
    assert payload["total_off_chain_btc"] == 0.01
    assert payload["total_accelerations"] == 200


def test_a_month_with_a_real_gap_carries_the_range_that_would_finish_it():
    """A hole inside the read range: only a backfill closes it."""
    covered = ex.merge_spans([(ts("2024-01-01"), ts("2024-03-10")),
                              (ts("2024-04-01"), ts("2024-09-01"))])
    _, skipped = ex.build([row("2024-03")], covered)
    assert skipped[0]["reason"] == "gap"
    assert skipped[0]["fetch_since"] == "2024-03-10"
    assert skipped[0]["fetch_until"] == "2024-04-01"


def test_the_current_month_is_filling_not_a_gap():
    """It needs no backfill, and must not be offered a range in the future."""
    covered = [(ts("2024-01-01"), ts("2024-08-14"))]
    _, skipped = ex.build([row("2024-08")], covered)
    assert skipped[0]["reason"] == "filling"
    assert "fetch_since" not in skipped[0]


def test_the_payload_has_no_timestamp_in_it():
    """It is committed, so an identical run must produce an identical file."""
    covered = [(ts("2024-01-01"), ts("2024-03-01"))]
    a, _ = ex.build([row("2024-01")], covered)
    b, _ = ex.build([row("2024-01")], covered)
    assert a == b
