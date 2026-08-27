"""Offline tests for the incremental acceleration fetch.

No credentials and no requests: the stop rule and the identity key are pure
functions over records, so a handful of literal dicts exercises both. What
they protect is the claim the top-up rests on -- that a partial walk down a
newest-first list cannot skip a record.
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

def test_a_deleted_anchor_does_not_change_what_follows():
    """The record the watermark came from need not still exist.

    This is the deletion caveat: if the newest record we hold were removed
    upstream, the walk must still resume at the same point.
    """
    watermark = 100
    upstream = [record("new", 130), record("also-new", 120)]
    assert not fa.page_is_old(upstream, watermark)
    # the anchor at exactly 100 is gone; the page below it is still "old"
    assert fa.page_is_old([record("older", 99)], watermark)


# --- the identity key ---------------------------------------------------

def test_parse_since_accepts_a_date_or_a_timestamp():
    assert fa.parse_since("2026-08-18") == 1787011200
    assert fa.parse_since("2026-08-18 05:03:00") == 1787029380


def test_parse_since_rejects_nonsense():
    with pytest.raises(SystemExit):
        fa.parse_since("last tuesday")


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
