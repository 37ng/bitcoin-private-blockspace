"""Offline tests for the acceleration fetch.

No credentials and, apart from one fake list, no requests. What these protect
is the invariant every other piece of the pipeline leans on: the table holds
one contiguous run of the history, so `MIN(added)` and `MAX(added)` bound a
stretch with nothing missing inside it. If a fetch could leave a gap, the
export would publish a half-fetched month as a finished one.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fetch_accelerations as fa


def record(txid, added, status="completed"):
    return {"txid": txid, "added": added, "status": status}


# --- what may be stored --------------------------------------------------

def test_a_settled_record_is_stored():
    assert fa.is_settled(record("a", 100, "completed"))
    assert fa.is_settled(record("a", 100, "completed_provisional"))
    assert fa.is_settled(record("a", 100, "failed"))


def test_an_in_flight_record_is_skipped():
    """Storing it would freeze it.

    Loads only append, keyed on `(txid, added)`, so an `accelerating` record
    written now keeps that status for good and never counts as revenue however
    it actually ended up. A later run reads it again, settled.
    """
    assert not fa.is_settled(record("a", 100, "accelerating"))


def test_an_unknown_status_is_treated_as_in_flight():
    """Erring towards re-reading a record beats freezing a state we don't know."""
    assert not fa.is_settled(record("a", 100, "queued_somehow"))
    assert not fa.is_settled({"txid": "a", "added": 100})


def test_collect_drops_in_flight_records_but_still_reports_their_keys():
    """The top-up's stop rule counts every record read, settled or not.

    A page of in-flight records still proves the walk reached that part of the
    list, which is what the join test needs to know.
    """
    records, seen, keys = [], set(), set()
    batch = [record("a", 100), record("b", 90, "accelerating")]
    assert fa.collect(batch, records, seen, keys) == 1
    assert [r["txid"] for r in records] == ["a"]
    assert keys == {("a", 100), ("b", 90)}


# --- the identity key ----------------------------------------------------

def test_one_txid_can_hold_two_real_requests():
    """A retry after a failure shares the txid but not the time.

    Keying on txid alone would drop the second, which is a real record.
    Observed on page 2 of the live history: two `failed` requests for one
    transaction, 34 minutes apart.
    """
    first, retry = record("7046aca3", 1787000324), record("7046aca3", 1787002346)
    assert len({fa.key_of(first), fa.key_of(retry)}) == 2


def test_a_record_returned_twice_collapses():
    """The same record on two pages is one record, not two.

    `added` never changes, so the duplicate shares the whole key.
    """
    copy, same = record("04fdd997", 1712571182), record("04fdd997", 1712571182)
    assert len({fa.key_of(copy), fa.key_of(same)}) == 1


def test_collect_keeps_one_copy_of_a_record_seen_twice():
    records, seen = [], set()
    fa.collect([record("a", 100)], records, seen)
    fa.collect([record("a", 100)], records, seen)
    assert len(records) == 1


# --- reading bounds ------------------------------------------------------

def test_parse_time_accepts_a_date_or_a_timestamp():
    assert fa.parse_time("2026-08-18") == 1787011200
    assert fa.parse_time("2026-08-18 05:03:00") == 1787029380


def test_parse_time_rejects_nonsense():
    with pytest.raises(SystemExit):
        fa.parse_time("last tuesday")


def test_a_page_of_older_records_stops_a_downward_walk():
    assert fa.page_is_old([record("a", 100), record("b", 90)], 100)


def test_one_newer_record_keeps_a_downward_walk_going():
    assert not fa.page_is_old([record("a", 101), record("b", 90)], 100)


def test_a_record_without_a_time_counts_as_new():
    """Read an odd record again rather than risk stopping short of it."""
    assert not fa.page_is_old([record("a", None)], 100)


# --- the seek ------------------------------------------------------------

def fake_history(monkeypatch, added, page_length=50, status="completed"):
    """Serve a newest-first list of `added` values as pages, without requests."""
    added = sorted(added, reverse=True)
    pages = [added[i:i + page_length]
             for i in range(0, len(added), page_length)]
    calls = []

    def get_json(url, params=None, **kwargs):
        page = params["page"]
        calls.append(page)
        batch = pages[page - 1] if 1 <= page <= len(pages) else []
        return None, [record(f"tx{t}", t, status) for t in batch]

    monkeypatch.setattr(fa, "get_json", get_json)
    monkeypatch.setattr(fa, "history_size",
                        lambda *a, **k: (len(added), len(pages)))
    return len(pages), calls


def test_the_seek_lands_on_the_page_holding_the_bound(monkeypatch):
    """One record a minute for 600 pages; the bound sits on a known page."""
    times = [1_000_000 - 60 * i for i in range(30_000)]
    pages, calls = fake_history(monkeypatch, times)
    bound = 1_000_000 - 60 * 20_000        # page 401 opens here
    assert fa.seek_page(bound, 50, pages, sleep=0) == 401
    assert len(calls) < 15, "a 600-page list must not cost 600 requests"


def test_a_bound_above_the_newest_record_enters_at_page_one(monkeypatch):
    pages, _ = fake_history(monkeypatch, [1_000, 900, 800])
    assert fa.seek_page(5_000, 50, pages, sleep=0) == 1


def test_a_bound_below_the_oldest_record_enters_at_the_last_page(monkeypatch):
    pages, _ = fake_history(monkeypatch, [1_000_000 - 60 * i
                                          for i in range(200)])
    assert fa.seek_page(0, 50, pages, sleep=0) == pages


# --- the top-up must join the run ----------------------------------------

def test_the_top_up_stops_once_it_touches_records_already_held(monkeypatch):
    """The stop rule is the invariant, not a timestamp comparison."""
    times = [1_000_000 - 60 * i for i in range(5_000)]
    fake_history(monkeypatch, times)
    have = {(f"tx{t}", t) for t in times[300:]}     # everything below page 7
    got = fa.fetch_top_up(sleep=0, page_length=50, have=have, overlap=2)
    # It read past the join, so it holds every record newer than the run.
    assert set(times[:300]) <= {r["added"] for r in got}


def test_the_top_up_refuses_to_stop_short_of_the_run(monkeypatch):
    """A walk that never reaches known records would leave a gap.

    Loading it would put new records above a hole and quietly break the
    contiguity that MIN/MAX completeness depends on, so the run fails loudly
    instead.
    """
    times = [1_000_000 - 60 * i for i in range(300)]
    fake_history(monkeypatch, times)
    with pytest.raises(SystemExit, match="contiguous"):
        fa.fetch_top_up(sleep=0, page_length=50,
                        have={("nothing-here", 1)}, overlap=2)


def test_the_top_up_reads_past_the_join_by_the_overlap(monkeypatch):
    """A record that shifted pages mid-walk must still be seen."""
    times = [1_000_000 - 60 * i for i in range(5_000)]
    _, calls = fake_history(monkeypatch, times)
    have = {(f"tx{t}", t) for t in times[100:]}     # join lands on page 3
    fa.fetch_top_up(sleep=0, page_length=50, have=have, overlap=2)
    assert max(calls) >= 5, "must read overlap pages beyond the first touch"


# --- the backfill must join the run --------------------------------------

def test_the_backfill_extends_the_run_downwards_without_a_gap(monkeypatch):
    """Everything between the target and the old bottom must come back."""
    times = [1_000_000 - 60 * i for i in range(5_000)]
    fake_history(monkeypatch, times)
    oldest_held = times[2_000]
    target = times[3_500]
    got = fa.fetch_back_to(sleep=0, page_length=50, oldest_held=oldest_held,
                           target=target, overlap=2, max_pages=0)
    added = {r["added"] for r in got}
    assert set(times[2_000:3_501]) <= added, "left a hole above the target"


def test_the_backfill_starts_above_the_anchor_so_the_join_overlaps(monkeypatch):
    """Meeting the run exactly would rely on page numbers holding still."""
    times = [1_000_000 - 60 * i for i in range(5_000)]
    fake_history(monkeypatch, times)
    got = fa.fetch_back_to(sleep=0, page_length=50, oldest_held=times[2_000],
                           target=times[2_500], overlap=2, max_pages=0)
    assert max(r["added"] for r in got) > times[2_000], "no overlap with the run"


def test_a_backfill_stores_no_in_flight_records(monkeypatch):
    times = [1_000_000 - 60 * i for i in range(500)]
    fake_history(monkeypatch, times, status="accelerating")
    got = fa.fetch_back_to(sleep=0, page_length=50, oldest_held=times[100],
                           target=times[300], overlap=2, max_pages=0)
    assert got == []
