"""Offline tests for the incremental acceleration fetch.

No credentials and, apart from one fake list, no requests: the stop rule, the
window and the identity key are pure functions over records, so a handful of
literal dicts exercises them. What they protect is the claim every partial
walk down a newest-first list rests on -- that it cannot skip a record.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fetch_accelerations as fa


def record(txid, added):
    return {"txid": txid, "added": added}


# --- the stop rule ------------------------------------------------------

def test_page_of_older_records_stops_the_walk():
    page = [record("a", 100), record("b", 90)]
    assert fa.page_is_old(page, 100)


def test_one_newer_record_keeps_the_walk_going():
    page = [record("a", 101), record("b", 90)]
    assert not fa.page_is_old(page, 100)


def test_a_record_without_a_time_counts_as_new():
    """Read an odd record again rather than risk stopping short of it."""
    assert not fa.page_is_old([record("a", None)], 100)


def test_the_watermark_itself_is_old():
    """`added > watermark` selects what follows, so equality must not requeue."""
    assert fa.page_is_old([record("a", 100)], 100)


# --- the watermark is a comparison, not a lookup ------------------------

def test_the_watermark_is_a_comparison_not_a_lookup():
    """Resuming must not depend on finding a particular record.

    Nothing has to still sit at the watermark timestamp for the walk to pick
    up the right place. An identity-based resume would break here; a
    comparison does not.
    """
    watermark = 100
    upstream = [record("new", 130), record("also-new", 120)]
    assert not fa.page_is_old(upstream, watermark)
    # the anchor at exactly 100 is gone; the page below it is still "old"
    assert fa.page_is_old([record("older", 99)], watermark)


# --- the identity key ---------------------------------------------------

def test_parse_time_accepts_a_date_or_a_timestamp():
    assert fa.parse_time("2026-08-18") == 1787011200
    assert fa.parse_time("2026-08-18 05:03:00") == 1787029380


def test_parse_time_rejects_nonsense():
    with pytest.raises(SystemExit):
        fa.parse_time("last tuesday")


def test_one_txid_can_hold_two_real_requests():
    """A retry after a failure shares the txid but not the time.

    Keying on txid alone would drop the second, which is a real record.
    Observed on page 2 of the live history: two `failed` requests for one
    transaction, 34 minutes apart.
    """
    first = record("7046aca3", 1787000324)
    retry = record("7046aca3", 1787002346)
    keys = {(r["txid"], r["added"]) for r in (first, retry)}
    assert len(keys) == 2


def test_a_record_returned_twice_collapses():
    """The same record on two pages is one record, not two.

    `added` never changes, so the duplicate shares the whole key. One such
    pair exists among completed records and double-counts 60,000 sats.
    """
    copy = record("04fdd997", 1712571182)
    same = record("04fdd997", 1712571182)
    keys = {(r["txid"], r["added"]) for r in (copy, same)}
    assert len(keys) == 1


# --- what the watermark does and does not decide ------------------------

def test_the_watermark_stops_the_read_but_never_filters_the_keep():
    """A record whose `added` equals the watermark is still loaded.

    Two accelerations can share a second. Its page counts as old and stops the
    walk, but the record is kept, because keeping is decided by key. A rule
    that filtered on `added > watermark` would lose it silently.
    """
    watermark = 100
    page = [record("anchor", 100), record("same-second", 100)]
    assert fa.page_is_old(page, watermark)          # the walk stops here
    have = {("anchor", 100)}                        # ...but this is still new
    assert [r["txid"] for r in fa.unloaded(page, have)] == ["same-second"]


# --- the range window ---------------------------------------------------

def test_the_window_keeps_both_bounds():
    """Inclusive at both ends.

    The lower bound must be inclusive for the same reason the watermark is:
    the walk stops on a page whose newest record sits exactly there, so a
    second acceleration in that same second has no other run to read it.
    """
    assert fa.in_window(record("on-since", 100), 100, 200)
    assert fa.in_window(record("inside", 150), 100, 200)
    assert fa.in_window(record("on-until", 200), 100, 200)


def test_the_window_drops_what_lies_outside_it():
    assert not fa.in_window(record("older", 99), 100, 200)
    assert not fa.in_window(record("newer", 201), 100, 200)


def test_a_record_without_a_time_is_kept():
    """It cannot be placed, and one loaded twice beats one lost."""
    assert fa.in_window(record("odd", None), 100, 200)


# --- the seek ------------------------------------------------------------

def fake_history(monkeypatch, added, page_length=50):
    """Serve a newest-first list of `added` values as pages, without requests."""
    added = sorted(added, reverse=True)
    pages = [added[i:i + page_length]
             for i in range(0, len(added), page_length)]
    calls = []

    def get_json(url, params=None, **kwargs):
        page = params["page"]
        calls.append(page)
        batch = pages[page - 1] if 1 <= page <= len(pages) else []
        return None, [record(f"tx{t}", t) for t in batch]

    monkeypatch.setattr(fa, "get_json", get_json)
    return len(pages), calls


def test_the_seek_lands_on_the_page_holding_the_bound(monkeypatch):
    """One record a minute for 600 pages; the bound sits on a known page."""
    times = [1_000_000 - 60 * i for i in range(30_000)]
    pages, calls = fake_history(monkeypatch, times)
    # page 401 opens at index 20_000, i.e. 1_000_000 - 60 * 20_000
    bound = 1_000_000 - 60 * 20_000
    assert fa.seek_page(bound, 50, pages, sleep=0) == 401
    assert len(calls) < 15, "a 600-page list must not cost 600 requests"


def test_a_bound_above_the_newest_record_enters_at_page_one(monkeypatch):
    pages, _ = fake_history(monkeypatch, [1_000, 900, 800])
    assert fa.seek_page(5_000, 50, pages, sleep=0) == 1


def test_a_bound_below_the_oldest_record_enters_at_the_last_page(monkeypatch):
    times = [1_000_000 - 60 * i for i in range(200)]
    pages, _ = fake_history(monkeypatch, times)
    assert fa.seek_page(0, 50, pages, sleep=0) == pages


def test_the_walk_reads_every_record_between_the_bounds(monkeypatch):
    """The seek and the window together must lose nothing in the middle."""
    times = [1_000_000 - 60 * i for i in range(5_000)]
    pages, _ = fake_history(monkeypatch, times)
    monkeypatch.setattr(fa, "history_size", lambda *a, **k: (len(times), pages))
    since, until = 1_000_000 - 60 * 3_000, 1_000_000 - 60 * 1_000
    got = fa.fetch_range(sleep=0, page_length=50, since=since, until=until,
                         overlap=2, max_pages=0)
    assert [r["added"] for r in got] == [t for t in times
                                         if since <= t <= until]
